from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from motata_cli.common.auth import AuthContext, resolve_auth

from motata_cli.meta.client import MetaClient

from types import SimpleNamespace

from motata_cli.common.config import JOBS_DIR
from motata_cli.common.errors import CliError
from motata_cli.common.utils import normalize_account_id, write_json_file
from motata_cli.meta.utils import ad_account_path
from motata_cli.meta.payloads import normalize_promoted_object
from motata_cli.meta.services import discovery, media, migration_assets, resources
from motata_cli.meta.services.creatives import create_creative


class MigrationDependencies(SimpleNamespace):
    """Explicit dependency snapshot; callers may override ports or supply a legacy module.

    Defaults come only from business services, never from the CLI. Constructed per
    invocation so patches to low-level services are honored without global mutation.
    """

    def __init__(self, **overrides: Any) -> None:
        # preflight also uses resource services; resolve it after package initialization.
        from motata_cli.meta.preflight import remap_promoted_object_for_target, validate_migration_preflight

        ports = dict(
            JOBS_DIR=JOBS_DIR, CliError=CliError, MetaClient=MetaClient,
            META_VERSION=media.META_VERSION, build_auth_from_args=build_auth_from_args,
            normalize_account_id=normalize_account_id, ad_account_path=ad_account_path,
            write_json_file=write_json_file, normalize_promoted_object=normalize_promoted_object,
            remap_promoted_object_for_target=remap_promoted_object_for_target,
            validate_migration_preflight=validate_migration_preflight,
            create_creative=create_creative,
        )
        for module, names in (
            (resources, ('get_entity', 'list_entities', 'create_campaign', 'create_adset', 'create_ad')),
            (discovery, ('infer_promotable_pages', 'discover_pixels')),
            (media, ('detect_creative_migration_mode', 'download_file', 'upload_video', 'upload_image',
                     'pick_link', 'pick_creative_thumbnail_url', 'pick_creative_text_parts',
                     'local_media_extension_from_url')),
            (migration_assets, ('load_required_migration_export', 'referenced_creatives_from_asset_tree',
                                'MIGRATION_EXPORT_CAMPAIGN_FIELDS', 'MIGRATION_EXPORT_ADSET_FIELDS',
                                'MIGRATION_EXPORT_AD_FIELDS', 'MIGRATION_EXPORT_CREATIVE_FIELDS')),
        ):
            ports.update({name: getattr(module, name) for name in names})
        unknown = overrides.keys() - ports.keys()
        if unknown:
            raise TypeError(f"Unknown migration dependencies: {sorted(unknown)}")
        super().__init__(**(ports | overrides))


def build_auth_from_args(account_id: str, access_token: str | None) -> AuthContext:
    return resolve_auth(account_id=account_id, access_token=access_token, media_code="facebook")


def export_migration_bundle(
    meta: MetaClient,
    *,
    source_account_id: str,
    campaign_ids: list[str],
    export_dir: Path,
    commands_module: Any = None,
) -> dict[str, Any]:
    commands = commands_module if commands_module is not None else MigrationDependencies()
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
    commands_module: Any = None,
) -> dict[str, Any]:
    commands = commands_module if commands_module is not None else MigrationDependencies()
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


def _execute_migration(args: argparse.Namespace, *, commands_module: Any, state: Any, bundle: Any) -> dict[str, Any]:
    export_dir = Path(args.export_dir).expanduser().resolve()
    media_dir = export_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    job_id = args.job_id
    state.stage("preflight")

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

    asset_tree, creatives_raw = bundle

    target_page_id = state.data.get("resolved_page_id") or args.page_id
    if not target_page_id:
        discovery = commands_module.infer_promotable_pages(target_meta, args.target_account_id)
        ad_attachable = [page for page in discovery["pages"] if page["ad_attach_usable"]]
        token_visible = [page for page in discovery["pages"] if page["token_visible"]]
        chosen = ad_attachable[0] if ad_attachable else (token_visible[0] if token_visible else None)
        if not chosen:
            raise commands_module.CliError("Could not discover a target page. Provide --page-id.")
        target_page_id = chosen["page_id"]

    state.data["resolved_page_id"] = target_page_id
    state.save()
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
    # Target compatibility is checked by the actual PAUSED creations below.
    # Legacy probes create/delete temporary objects outside the ledger and are unsafe here.
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

    state.stage("media")
    existing_creatives: list[dict[str, Any]] = []
    existing_campaigns: list[dict[str, Any]] = []
    existing_adsets: list[dict[str, Any]] = []
    existing_ads: list[dict[str, Any]] = []

    for old_video_id in video_ids:
        if state.confirmed("video", old_video_id):
            continue
        dest = media_dir / f"video_{old_video_id}.mp4"
        if not dest.exists():
            meta = source_meta.get(old_video_id, params={"fields": "id,source"})
            temporary = dest.with_suffix(".part")
            commands_module.download_file(meta["source"], temporary)
            temporary.replace(dest)

    new_video_map: dict[str, str] = {}
    for old_video_id in video_ids:
        uploaded = state.write("video", old_video_id, {"source_video_id": old_video_id}, lambda: commands_module.upload_video(
            target_meta, args.target_account_id,
            str(media_dir / f"video_{old_video_id}.mp4"), name=f"video_{old_video_id}.mp4",
        ))
        new_video_map[str(old_video_id)] = str(uploaded.get("id"))

    uploaded_image_hash_by_url: dict[str, str] = {}
    uploaded_image_hashes: dict[str, str] = {
        entry["source_id"]: entry["target_id"]
        for entry in state.data["ledger"].values()
        if entry["kind"] == "image" and entry["status"] == "confirmed"
    }
    newly_created_creative_ids: list[str] = []

    def ensure_uploaded_image_hash(*, old_creative_id: str, creative: dict[str, Any]) -> str:
        confirmed = state.confirmed("image", old_creative_id)
        if confirmed:
            uploaded_image_hashes[old_creative_id] = confirmed
            return confirmed
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
            temporary = image_path.with_suffix(".part")
            commands_module.download_file(thumb_url, temporary)
            temporary.replace(image_path)
        image_upload = state.write("image", old_creative_id, {"url": thumb_url}, lambda: commands_module.upload_image(
            target_meta, args.target_account_id, str(image_path), image_path.name,
        ), result_key="hash")
        image_hash = str(image_upload["hash"])
        uploaded_image_hash_by_url[thumb_url] = image_hash
        uploaded_image_hashes[old_creative_id] = image_hash
        return image_hash

    state.stage("creatives")
    old_to_new_creatives: dict[str, str] = {}
    for old_creative_id, creative in matched_creatives.items():
        confirmed = state.confirmed("creative", str(old_creative_id))
        if confirmed:
            old_to_new_creatives[str(old_creative_id)] = confirmed
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
        created = state.write("creative", str(old_creative_id), vars(creative_args), lambda: commands_module.create_creative(target_meta, args.target_account_id, creative_args))
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

    state.stage("hierarchy")

    old_to_new_campaigns: dict[str, str] = {}
    old_to_new_adsets: dict[str, str] = {}
    old_to_new_ads: dict[str, str] = {}

    for campaign_node in asset_tree["tree"]:
        old_campaign = campaign_node["campaign"]
        existing_campaign = None  # Only the source-ID ledger can establish identity.
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
            created_campaign = state.write("campaign", str(old_campaign["id"]), vars(campaign_args), lambda: commands_module.create_campaign(target_meta, args.target_account_id, campaign_args))
            new_campaign_id = str(created_campaign["id"])
            existing_campaigns.append({"id": new_campaign_id, "name": old_campaign["name"]})
        old_to_new_campaigns[str(old_campaign["id"])] = new_campaign_id

        for adset_node in campaign_node["adsets"]:
            old_adset = adset_node["adset"]
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
                created_adset = state.write("adset", str(old_adset["id"]), vars(adset_args), lambda: commands_module.create_adset(target_meta, args.target_account_id, adset_args))
                new_adset_id = str(created_adset["id"])
                existing_adsets.append({"id": new_adset_id, "name": old_adset["name"], "campaign_id": new_campaign_id})
            old_to_new_adsets[str(old_adset["id"])] = new_adset_id

            for ad_node in adset_node["ads"]:
                old_ad = ad_node["ad"]
                old_creative = ad_node["creative"]
                new_creative_id = old_to_new_creatives[str(old_creative["id"])]
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
                    created_ad = state.write("ad", str(old_ad["id"]), vars(ad_args), lambda: commands_module.create_ad(target_meta, args.target_account_id, ad_args))
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
    result["job_path"] = str(state.path)
    return result


_CONFIG_FIELDS = (
    "source_account_id", "target_account_id", "page_id", "instagram_user_id",
    "pixel_id", "target_application_id", "target_object_store_url",
    "promoted_object_overrides_json",
)


def run_migration_flow(args: argparse.Namespace, *, commands_module: Any = None) -> dict[str, Any]:
    commands_module = commands_module if commands_module is not None else MigrationDependencies()
    from .migration_state import (
        SCHEMA_VERSION, MigrationState, MigrationStateError, fingerprint,
        job_lock, job_path, load_job,
    )

    args = argparse.Namespace(**vars(args))
    args.job_id = args.job_id or f"mig_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    path = job_path(commands_module.JOBS_DIR, args.job_id)
    args.export_dir = str(Path(args.export_dir).expanduser().resolve())
    # Names are not a remote identity or proof of ownership.
    args.reuse_existing_by_name = False
    config = {field: getattr(args, field, None) for field in _CONFIG_FIELDS}
    config.update(export_dir=args.export_dir, reuse_existing_by_name=False)
    with job_lock(path):
        if path.exists():
            data = load_job(path)
            if data["config"] != config:
                raise MigrationStateError("Job configuration differs; refusing to reuse job ID")
        else:
            if getattr(args, "_resume", False):
                raise MigrationStateError("Job not found; resume cannot create a new job")
            data = {
                "schema_version": SCHEMA_VERSION, "job_id": args.job_id,
                "type": "migration", "status": "running", "config": config,
                "export_fingerprint": "", "ledger": {}, "stages": {},
            }
        state = MigrationState(path, data)
        state.save()
        try:
            bundle = commands_module.load_required_migration_export(Path(args.export_dir))
            digest = fingerprint(bundle)
            if data["export_fingerprint"] and data["export_fingerprint"] != digest:
                raise MigrationStateError("Export changed; unsafe resume refused")
            data["export_fingerprint"] = digest
            uncertain = [e for e in data["ledger"].values() if e["status"] != "confirmed"]
            if uncertain:
                data["status"] = "needs_review"
                raise MigrationStateError("needs_review: uncertain remote writes have no authoritative correlation ID; remote name lookup is unsafe, no writes retried")
            if data["status"] == "completed":
                return {**data["result"], "cleanup_plan": data["cleanup_plan"]}
            data["status"] = "running"
            state.save()
            result = _execute_migration(args, commands_module=commands_module, state=state, bundle=bundle)
            data["status"] = "completed"
            if data.get("stage"):
                data["stages"][data["stage"]] = "completed"
            data["result"] = result
            data.pop("error", None)
            state.save()
            result["cleanup_plan"] = data["cleanup_plan"]
            return result
        except BaseException as exc:
            if data["status"] != "needs_review":
                data["status"] = "failed"
            if data.get("stage"):
                data["stages"][data["stage"]] = data["status"]
            # Never persist exception text: upstream errors may contain signed URLs/tokens.
            data["error"] = {"type": type(exc).__name__, "recommendation": "Inspect job status and cleanup_plan before resuming."}
            state.save()
            if isinstance(exc, Exception):
                raise commands_module.CliError(f"Migration {args.job_id} {data['status']}; checkpoint: {path}. Inspect status/cleanup_plan; no automatic deletion.") from exc
            raise
