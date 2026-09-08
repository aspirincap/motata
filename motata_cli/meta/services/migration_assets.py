"""Migration export schema and local asset helpers."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from motata_cli.common.errors import CliError
from motata_cli.common.utils import load_json_file, normalize_name

def load_required_migration_export(export_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    asset_tree_path = export_dir / "asset-tree.json"
    creatives_path = export_dir / "creatives.raw.json"
    missing = [str(path) for path in (asset_tree_path, creatives_path) if not path.exists()]
    if missing:
        raise CliError(
            "Migration export is incomplete. Expected files:\n"
            f"- {asset_tree_path}\n"
            f"- {creatives_path}\n"
            f"Missing: {', '.join(missing)}\n"
            "Generate them with `motata meta migrate export`, then rerun the migration command."
        )

    asset_tree = load_json_file(asset_tree_path)
    creatives = load_json_file(creatives_path)
    if not isinstance(asset_tree, dict) or not isinstance(asset_tree.get("tree"), list):
        raise CliError(f"Invalid migration export: {asset_tree_path} must contain a top-level 'tree' list.")
    if not isinstance(creatives, dict):
        raise CliError(f"Invalid migration export: {creatives_path} must contain a JSON object keyed by creative ID.")
    return asset_tree, creatives


MIGRATION_EXPORT_CAMPAIGN_FIELDS = [
    "id",
    "name",
    "objective",
    "status",
    "daily_budget",
    "lifetime_budget",
    "bid_strategy",
    "special_ad_categories",
]


MIGRATION_EXPORT_ADSET_FIELDS = [
    "id",
    "name",
    "campaign_id",
    "status",
    "effective_status",
    "optimization_goal",
    "billing_event",
    "promoted_object",
    "targeting",
    "daily_budget",
    "lifetime_budget",
    "bid_amount",
    "bid_strategy",
    "bid_constraints",
    "start_time",
    "end_time",
    "destination_type",
]


MIGRATION_EXPORT_AD_FIELDS = [
    "id",
    "name",
    "campaign_id",
    "adset_id",
    "status",
    "effective_status",
    "bid_amount",
    "creative",
]


MIGRATION_EXPORT_CREATIVE_FIELDS = [
    "id",
    "name",
    "object_story_spec",
    "object_story_id",
    "asset_feed_spec",
    "media_sourcing_spec",
    "url_tags",
    "thumbnail_url",
    "image_url",
    "link_url",
    "body",
    "title",
    "call_to_action_type",
    "object_type",
    "actor_id",
]


def first_by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    target = normalize_name(name)
    for item in items:
        current = normalize_name(item.get("name"))
        if current == target or current.startswith(target):
            return item
    return None


def first_creative_by_prefix(items: list[dict[str, Any]], name: str, page_id: str | None) -> dict[str, Any] | None:
    target = normalize_name(name)
    for item in items:
        item_page = str(((item.get("object_story_spec") or {}).get("page_id")) or "")
        if page_id and item_page and item_page != str(page_id):
            continue
        if normalize_name(item.get("name")).startswith(target):
            return item
    return None


def referenced_creatives_from_asset_tree(asset_tree: dict[str, Any], creatives: dict[str, Any]) -> dict[str, Any]:
    referenced_ids: set[str] = set()
    for campaign_node in asset_tree["tree"]:
        for adset_node in campaign_node["adsets"]:
            for ad_node in adset_node["ads"]:
                creative = ad_node["creative"]
                referenced_ids.add(str(creative["id"]))
    missing = sorted(referenced_ids - creatives.keys())
    if missing:
        raise CliError("Migration export is incomplete: missing referenced creatives: " + ", ".join(missing))
    for creative_id in referenced_ids:
        creative = creatives[creative_id]
        if not isinstance(creative, dict) or str(creative.get("id") or "") != creative_id:
            raise CliError(f"Migration export has an invalid or mismatched creative ID: {creative_id}")
    return {cid: creatives[cid] for cid in sorted(referenced_ids)}
