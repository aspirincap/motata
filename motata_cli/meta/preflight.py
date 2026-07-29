from __future__ import annotations

import argparse
import copy
import json
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from motata_cli.meta.client import MetaClient


def _commands_module() -> Any:
    # Import lazily so this module can reuse the existing command helpers
    # without introducing an import cycle during module initialization.
    from motata_cli.meta import commands as meta_commands

    return meta_commands


def remap_promoted_object_for_target(
    promoted_object: dict[str, Any] | None,
    *,
    target_page_id: str | None = None,
    target_pixel_id: str | None = None,
    target_application_id: str | None = None,
    target_object_store_url: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mapped = copy.deepcopy(promoted_object or {})
    if mapped.get("page_id") and target_page_id:
        mapped["page_id"] = str(target_page_id)
    if mapped.get("pixel_id") and target_pixel_id:
        mapped["pixel_id"] = str(target_pixel_id)
    if target_application_id:
        mapped["application_id"] = str(target_application_id)
    if target_object_store_url:
        mapped["object_store_url"] = target_object_store_url
    if overrides:
        mapped.update(copy.deepcopy(overrides))
    return mapped


def describe_validation_error(exc: Exception) -> str:
    payload = _commands_module().parse_meta_error_payload(exc)
    if payload:
        title = payload.get("title")
        message = payload.get("message")
        if title and message and title != message:
            return f"{title}: {message}"
        return str(title or message or exc)
    return str(exc)


def build_validation_campaign_args(source_campaign: dict[str, Any], *, name: str) -> argparse.Namespace:
    return argparse.Namespace(
        name=name,
        objective=source_campaign.get("objective"),
        status="PAUSED",
        daily_budget=source_campaign.get("daily_budget"),
        lifetime_budget=source_campaign.get("lifetime_budget"),
        bid_strategy=source_campaign.get("bid_strategy"),
        bid_cap=source_campaign.get("bid_cap"),
        special_ad_categories=source_campaign.get("special_ad_categories") or [],
        use_adset_level_budgets=False,
        bid_constraints_json=None,
    )


def build_validation_adset_args(
    source_adset: dict[str, Any],
    *,
    campaign_id: str,
    promoted_object: dict[str, Any],
    name: str,
) -> argparse.Namespace:
    commands = _commands_module()
    bid_constraints = source_adset.get("bid_constraints")
    return argparse.Namespace(
        campaign_id=campaign_id,
        name=name,
        optimization_goal=source_adset.get("optimization_goal"),
        billing_event=source_adset.get("billing_event"),
        status="PAUSED",
        targeting_json=commands.json_compact(source_adset.get("targeting") or {}),
        promoted_object_json=commands.json_compact(promoted_object),
        daily_budget=source_adset.get("daily_budget"),
        lifetime_budget=source_adset.get("lifetime_budget"),
        bid_amount=source_adset.get("bid_amount"),
        bid_strategy=source_adset.get("bid_strategy"),
        bid_constraints_json=commands.json_compact(bid_constraints) if bid_constraints else None,
        start_time=source_adset.get("start_time"),
        end_time=source_adset.get("end_time"),
        destination_type=source_adset.get("destination_type"),
        frequency_cap_json=None,
        frequency_control_specs_json=None,
    )


def validate_promoted_object_probe(
    meta: MetaClient,
    account_id: str,
    *,
    source_campaign: dict[str, Any],
    source_adset: dict[str, Any],
    promoted_object: dict[str, Any],
    cleanup: bool = True,
) -> dict[str, Any]:
    commands = _commands_module()
    campaign_name = f"motata-validate-campaign-{uuid.uuid4().hex[:8]}"
    campaign = commands.create_campaign(
        meta,
        account_id,
        build_validation_campaign_args(source_campaign, name=campaign_name),
    )
    result: dict[str, Any] = {"ok": True, "campaign": campaign}
    try:
        validate_args = build_validation_adset_args(
            source_adset,
            campaign_id=campaign["id"],
            promoted_object=promoted_object,
            name=f"motata-validate-adset-{uuid.uuid4().hex[:8]}",
        )
        result["adset"] = commands.create_adset(meta, account_id, validate_args)
        return result
    finally:
        adset = result.get("adset") or {}
        if cleanup and adset.get("id"):
            try:
                result["adset_cleanup"] = commands.cleanup_object(meta, adset["id"])
            except Exception as cleanup_exc:  # pragma: no cover - best effort cleanup
                result["adset_cleanup_error"] = str(cleanup_exc)
        if cleanup and campaign.get("id"):
            try:
                result["campaign_cleanup"] = commands.cleanup_object(meta, campaign["id"])
            except Exception as cleanup_exc:  # pragma: no cover - best effort cleanup
                result["campaign_cleanup_error"] = str(cleanup_exc)


def validate_target_promoted_objects(
    meta: MetaClient,
    account_id: str,
    asset_tree: dict[str, Any],
    *,
    target_page_id: str | None,
    target_pixel_id: str | None,
    target_application_id: str | None = None,
    target_object_store_url: str | None = None,
    promoted_object_overrides: dict[str, Any] | None = None,
) -> None:
    blockers: list[str] = []
    seen: set[str] = set()
    for campaign_node in asset_tree["tree"]:
        campaign = campaign_node.get("campaign") or {}
        campaign_label = str(campaign.get("id") or campaign.get("name") or "<unknown campaign>")
        for adset_node in campaign_node["adsets"]:
            adset = adset_node.get("adset") or {}
            adset_label = str(adset.get("id") or adset.get("name") or "<unknown adset>")
            source_promoted_object = adset.get("promoted_object") or {}
            if not source_promoted_object:
                continue
            promoted_object = remap_promoted_object_for_target(
                source_promoted_object,
                target_page_id=target_page_id,
                target_pixel_id=target_pixel_id,
                target_application_id=target_application_id,
                target_object_store_url=target_object_store_url,
                overrides=promoted_object_overrides,
            )
            validation_key = json.dumps(
                {
                    "campaign": {
                        "objective": campaign.get("objective"),
                        "daily_budget": campaign.get("daily_budget"),
                        "lifetime_budget": campaign.get("lifetime_budget"),
                        "bid_strategy": campaign.get("bid_strategy"),
                        "bid_cap": campaign.get("bid_cap"),
                        "special_ad_categories": campaign.get("special_ad_categories") or [],
                    },
                    "adset": {
                        "optimization_goal": adset.get("optimization_goal"),
                        "billing_event": adset.get("billing_event"),
                        "targeting": adset.get("targeting") or {},
                        "daily_budget": adset.get("daily_budget"),
                        "lifetime_budget": adset.get("lifetime_budget"),
                        "bid_amount": adset.get("bid_amount"),
                        "bid_strategy": adset.get("bid_strategy"),
                        "bid_constraints": adset.get("bid_constraints"),
                        "start_time": adset.get("start_time"),
                        "end_time": adset.get("end_time"),
                        "destination_type": adset.get("destination_type"),
                    },
                    "promoted_object": promoted_object,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if validation_key in seen:
                continue
            seen.add(validation_key)
            try:
                validate_promoted_object_probe(
                    meta,
                    account_id,
                    source_campaign=campaign,
                    source_adset=adset,
                    promoted_object=promoted_object,
                    cleanup=True,
                )
            except Exception as exc:
                blockers.append(
                    f"Campaign {campaign_label} / ad set {adset_label} failed target promoted-object validation after applying target mappings: "
                    f"{describe_validation_error(exc)}. "
                    "Provide compatible --page-id / --pixel-id / --target-application-id / --target-object-store-url values, "
                    "or override promoted_object fields with --promoted-object-overrides."
                )
    if blockers:
        raise _commands_module().CliError(
            "Migration blocked by target promoted-object validation:\n- " + "\n- ".join(blockers)
        )


def adset_uses_app_promoted_object(campaign: dict[str, Any], adset: dict[str, Any]) -> bool:
    promoted_object = adset.get("promoted_object") or {}
    return bool(
        promoted_object.get("application_id")
        or promoted_object.get("object_store_url")
        or adset.get("destination_type") == "APP"
        or campaign.get("objective") == "OUTCOME_APP_PROMOTION"
        or adset.get("optimization_goal") == "APP_INSTALLS"
    )


def validate_ad_link_probe(
    meta: MetaClient,
    account_id: str,
    *,
    source_campaign: dict[str, Any],
    source_adset: dict[str, Any],
    creative_id: str,
    cleanup: bool = True,
) -> dict[str, Any]:
    commands = _commands_module()
    result = validate_promoted_object_probe(
        meta,
        account_id,
        source_campaign=source_campaign,
        source_adset=source_adset,
        promoted_object=source_adset.get("promoted_object") or {},
        cleanup=False,
    )
    try:
        ad_args = argparse.Namespace(
            name=f"motata-validate-ad-{uuid.uuid4().hex[:8]}",
            adset_id=result["adset"]["id"],
            creative_id=creative_id,
            status="PAUSED",
            bid_amount=None,
            tracking_specs_json=None,
            conversion_domain=None,
        )
        result["ad"] = commands.create_ad(meta, account_id, ad_args)
        return result
    finally:
        ad = result.get("ad") or {}
        adset = result.get("adset") or {}
        campaign = result.get("campaign") or {}
        if cleanup and ad.get("id"):
            try:
                result["ad_cleanup"] = commands.cleanup_object(meta, ad["id"])
            except Exception as cleanup_exc:  # pragma: no cover - best effort cleanup
                result["ad_cleanup_error"] = str(cleanup_exc)
        if cleanup and adset.get("id"):
            try:
                result["adset_cleanup"] = commands.cleanup_object(meta, adset["id"])
            except Exception as cleanup_exc:  # pragma: no cover - best effort cleanup
                result["adset_cleanup_error"] = str(cleanup_exc)
        if cleanup and campaign.get("id"):
            try:
                result["campaign_cleanup"] = commands.cleanup_object(meta, campaign["id"])
            except Exception as cleanup_exc:  # pragma: no cover - best effort cleanup
                result["campaign_cleanup_error"] = str(cleanup_exc)


def validate_target_app_ad_links(
    meta: MetaClient,
    account_id: str,
    asset_tree: dict[str, Any],
    old_to_new_creatives: dict[str, str],
) -> None:
    blockers: list[str] = []
    seen: set[str] = set()
    for campaign_node in asset_tree["tree"]:
        campaign = campaign_node.get("campaign") or {}
        campaign_label = str(campaign.get("id") or campaign.get("name") or "<unknown campaign>")
        for adset_node in campaign_node["adsets"]:
            adset = adset_node.get("adset") or {}
            if not adset_uses_app_promoted_object(campaign, adset):
                continue
            adset_label = str(adset.get("id") or adset.get("name") or "<unknown adset>")
            for ad_node in adset_node["ads"]:
                ad = ad_node.get("ad") or {}
                creative = ad_node.get("creative") or {}
                old_creative_id = str(creative.get("id") or "")
                new_creative_id = old_to_new_creatives.get(old_creative_id)
                if not new_creative_id:
                    continue
                validation_key = json.dumps(
                    {
                        "campaign_id": campaign.get("id"),
                        "adset_id": adset.get("id"),
                        "new_creative_id": new_creative_id,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if validation_key in seen:
                    continue
                seen.add(validation_key)
                try:
                    validate_ad_link_probe(
                        meta,
                        account_id,
                        source_campaign=campaign,
                        source_adset=adset,
                        creative_id=new_creative_id,
                        cleanup=True,
                    )
                except Exception as exc:
                    ad_label = str(ad.get("id") or ad.get("name") or "<unknown ad>")
                    blockers.append(
                        f"Campaign {campaign_label} / ad set {adset_label} / ad {ad_label} failed target ad-link validation: "
                        f"{describe_validation_error(exc)}. "
                        "The recreated creative is not compatible with the target app/page/promoted-object combination."
                    )
    if blockers:
        raise _commands_module().CliError(
            "Migration blocked by target ad-link validation:\n- " + "\n- ".join(blockers)
        )


def validate_migration_preflight(
    asset_tree: dict[str, Any],
    *,
    target_application_id: str | None = None,
    target_object_store_url: str | None = None,
) -> None:
    blockers: list[str] = []
    if bool(target_application_id) != bool(target_object_store_url):
        blockers.append(
            "Target app mapping is incomplete. Provide both --target-application-id and --target-object-store-url together."
        )
    for campaign_node in asset_tree["tree"]:
        campaign = campaign_node.get("campaign") or {}
        campaign_label = str(campaign.get("id") or campaign.get("name") or "<unknown campaign>")
        campaign_objective = campaign.get("objective")
        for adset_node in campaign_node["adsets"]:
            adset = adset_node.get("adset") or {}
            adset_label = str(adset.get("id") or adset.get("name") or "<unknown adset>")
            promoted_object = adset.get("promoted_object") or {}
            if campaign_objective == "OUTCOME_SALES" and not promoted_object:
                blockers.append(
                    f"Campaign {campaign_label} / ad set {adset_label} is OUTCOME_SALES but has no promoted_object in the export package."
                )
            if adset.get("optimization_goal") == "APP_INSTALLS":
                missing_fields = [
                    field_name
                    for field_name in ("application_id", "object_store_url")
                    if not promoted_object.get(field_name)
                ]
                if missing_fields:
                    blockers.append(
                        f"Campaign {campaign_label} / ad set {adset_label} is APP_INSTALLS but promoted_object is missing: {', '.join(missing_fields)}."
                    )
    if blockers:
        raise _commands_module().CliError(
            "Migration blocked by preflight validation:\n- " + "\n- ".join(blockers)
        )
