from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from motata_cli.meta.specs import (
    AdSetSpec,
    AdSpec,
    CampaignSpec,
    CreativeSpec,
    ad_spec_from,
    adset_spec_from,
    campaign_spec_from,
    creative_spec_from,
)


def _commands_module() -> Any:
    # Import lazily so payload builders can reuse command-layer validators
    # without introducing an import cycle during module initialization.
    from motata_cli.meta import commands as meta_commands

    return meta_commands


def _normalize_json_text(raw: Any, label: str, expected_type: type | tuple[type, ...] = dict) -> str | None:
    commands = _commands_module()
    if raw in (None, ""):
        return None
    if isinstance(raw, str):
        value = commands.parse_json_option(raw, label, expected_type)
    elif isinstance(raw, expected_type):
        value = raw
    else:
        if isinstance(expected_type, tuple):
            expected_names = ", ".join(item.__name__ for item in expected_type)
        else:
            expected_names = expected_type.__name__
        raise commands.CliError(f"Invalid {label}: expected {expected_names}")
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_raw_payload(payload_json: str | None, payload_file: str | None, *, label: str) -> dict[str, Any]:
    commands = _commands_module()
    if payload_json and payload_file:
        raise commands.CliError(f"Specify only one of --payload-json or --payload-file for {label}")
    if payload_file:
        payload = commands.load_json_file(Path(payload_file))
        if not isinstance(payload, dict):
            raise commands.CliError(f"Invalid {label}: expected JSON object in {payload_file}")
        return copy.deepcopy(payload)
    payload = commands.parse_json_option(payload_json, label, dict)
    return copy.deepcopy(payload or {})


def _assign(payload: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        payload[key] = value


def _compact_payload_field(
    payload: dict[str, Any],
    field_name: str,
    *,
    label: str,
    expected_type: type | tuple[type, ...] = dict,
) -> None:
    if field_name not in payload or payload[field_name] in (None, ""):
        payload.pop(field_name, None)
        return
    payload[field_name] = _normalize_json_text(payload[field_name], label, expected_type)


def _optional_positive_int_str(value: Any, label: str) -> str | None:
    return _commands_module().parse_positive_int_str(value, label) if value not in (None, "") else None


def _payload_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(str(value))


def normalize_special_ad_categories(values: list[str] | None) -> list[str]:
    if not values:
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def parse_bid_constraints(raw: Any, label: str = "bid_constraints") -> dict[str, Any] | None:
    constraints = _commands_module().parse_json_option(raw, label, dict)
    if constraints is None:
        return None
    return constraints


def validate_bid_strategy_constraints(
    bid_strategy: str | None,
    *,
    bid_cap: int | None = None,
    bid_amount: int | None = None,
    bid_constraints: dict[str, Any] | None = None,
    label_prefix: str,
) -> None:
    commands = _commands_module()
    if bid_strategy in commands.BID_STRATEGIES_REQUIRING_BID_CAP:
        required_value = bid_cap if bid_cap is not None else bid_amount
        required_label = "bid_cap" if bid_cap is not None or label_prefix == "campaign" else "bid_amount"
        if required_value is None:
            raise commands.CliError(f"{label_prefix}: {required_label} required for {bid_strategy}")
    if bid_strategy == commands.BID_STRATEGY_MIN_ROAS:
        if not bid_constraints or bid_constraints.get("roas_average_floor") is None:
            raise commands.CliError(
                f"{label_prefix}: bid_constraints.roas_average_floor required for {commands.BID_STRATEGY_MIN_ROAS}"
            )


def normalize_targeting_spec(raw: Any, label: str = "targeting") -> dict[str, Any] | None:
    commands = _commands_module()
    targeting = commands.parse_json_option(raw, label, dict)
    if targeting is None:
        return None
    age_min = targeting.get("age_min")
    age_max = targeting.get("age_max")
    if age_min is not None:
        age_min = int(age_min)
        if age_min < 13 or age_min > 65:
            raise commands.CliError(f"{label}: age_min must be between 13 and 65")
        targeting["age_min"] = age_min
    if age_max is not None:
        age_max = int(age_max)
        if age_max < 13 or age_max > 65:
            raise commands.CliError(f"{label}: age_max must be between 13 and 65")
        targeting["age_max"] = age_max
    if age_min is not None and age_max is not None and age_min > age_max:
        raise commands.CliError(f"{label}: age_min cannot be greater than age_max")
    genders = targeting.get("genders")
    if genders is not None:
        if not isinstance(genders, list):
            raise commands.CliError(f"{label}: genders must be a list")
        normalized_genders = []
        for gender in genders:
            parsed = int(gender)
            if parsed < 0 or parsed > 2:
                raise commands.CliError(f"{label}: genders values must be 0, 1, or 2")
            normalized_genders.append(parsed)
        targeting["genders"] = normalized_genders
    geo_locations = targeting.get("geo_locations")
    if geo_locations is not None:
        if not isinstance(geo_locations, dict):
            raise commands.CliError(f"{label}: geo_locations must be an object")
        countries = geo_locations.get("countries")
        if countries is not None:
            if not isinstance(countries, list):
                raise commands.CliError(f"{label}: geo_locations.countries must be a list")
            normalized_countries = []
            for country in countries:
                code = str(country).strip().upper()
                if not re.fullmatch(r"[A-Z]{2}", code):
                    raise commands.CliError(f"{label}: invalid country code {country!r}")
                normalized_countries.append(code)
            geo_locations["countries"] = normalized_countries
        targeting["geo_locations"] = geo_locations
    targeting_automation = targeting.get("targeting_automation")
    if targeting_automation is None:
        targeting["targeting_automation"] = {"advantage_audience": 0}
    else:
        if not isinstance(targeting_automation, dict):
            raise commands.CliError(f"{label}: targeting_automation must be an object")
        advantage_audience = targeting_automation.get("advantage_audience")
        if advantage_audience not in (0, 1):
            raise commands.CliError(f"{label}: targeting_automation.advantage_audience must be 0 or 1")
    return targeting


def normalize_promoted_object(raw: Any, label: str = "promoted_object") -> dict[str, Any] | None:
    promoted_object = _commands_module().parse_json_option(raw, label, dict)
    if promoted_object is None:
        return None
    return promoted_object


def normalize_frequency_control_specs(raw: Any, label: str = "frequency_control_specs") -> list[dict[str, Any]] | None:
    value = _commands_module().parse_json_option(raw, label, list)
    if value is None:
        return None
    for item in value:
        if not isinstance(item, dict):
            raise _commands_module().CliError(f"{label}: each item must be an object")
    return value


def normalize_tracking_specs(raw: Any, label: str = "tracking_specs") -> list[dict[str, Any]] | None:
    commands = _commands_module()
    specs = commands.parse_json_option(raw, label, list)
    if specs is None:
        return None
    for item in specs:
        if not isinstance(item, dict):
            raise commands.CliError(f"{label}: each item must be an object")
        action_types = item.get("action.type")
        if not isinstance(action_types, list) or not all(isinstance(x, str) and x for x in action_types):
            raise commands.CliError(f"{label}: each item requires 'action.type' as a non-empty string list")
        fb_pixel = item.get("fb_pixel")
        if fb_pixel is not None and (not isinstance(fb_pixel, list) or not all(isinstance(x, str) and x for x in fb_pixel)):
            raise commands.CliError(f"{label}: fb_pixel must be a string list when provided")
    return specs


def build_campaign_payload(source: CampaignSpec | argparse.Namespace | dict[str, Any], *, update: bool = False) -> dict[str, Any]:
    commands = _commands_module()
    spec = campaign_spec_from(source)
    payload = _load_raw_payload(spec.payload_json, spec.payload_file, label="campaign payload")

    _assign(payload, "name", commands.validate_name(spec.name, "campaign name"))
    _assign(payload, "objective", spec.objective)
    if not update and "status" not in payload:
        payload["status"] = spec.status or "PAUSED"
    else:
        _assign(payload, "status", spec.status)
    _assign(payload, "daily_budget", _optional_positive_int_str(spec.daily_budget, "daily_budget"))
    _assign(payload, "lifetime_budget", _optional_positive_int_str(spec.lifetime_budget, "lifetime_budget"))
    _assign(payload, "bid_strategy", spec.bid_strategy)
    _assign(payload, "bid_cap", _optional_positive_int_str(spec.bid_cap, "bid_cap"))

    if spec.special_ad_categories:
        payload["special_ad_categories"] = commands.json_compact(normalize_special_ad_categories(spec.special_ad_categories))
    elif not update and "special_ad_categories" in payload:
        _compact_payload_field(payload, "special_ad_categories", label="special_ad_categories", expected_type=(list, dict))
    elif not update:
        payload["special_ad_categories"] = commands.json_compact([])

    if spec.use_adset_level_budgets:
        payload["is_adset_budget_sharing_enabled"] = "false"
        payload.pop("daily_budget", None)
        payload.pop("lifetime_budget", None)

    payload = commands.filter_empty(payload)
    if not update:
        payload["name"] = commands.validate_non_empty(payload.get("name"), "campaign name")
        payload["objective"] = commands.validate_non_empty(payload.get("objective"), "objective")
    if payload.get("daily_budget") and payload.get("lifetime_budget"):
        raise commands.CliError("campaign: cannot set both daily_budget and lifetime_budget")

    bid_constraints = parse_bid_constraints(spec.bid_constraints_json)
    if bid_constraints is None and payload.get("bid_constraints") not in (None, ""):
        bid_constraints = commands.parse_json_option(payload.get("bid_constraints"), "bid_constraints", dict)
    validate_bid_strategy_constraints(
        payload.get("bid_strategy"),
        bid_cap=_payload_int(payload.get("bid_cap")),
        bid_constraints=bid_constraints,
        label_prefix="campaign",
    )
    if update and not payload:
        raise commands.CliError("No update fields provided. Pass at least one update option.")
    return payload


def build_adset_payload(source: AdSetSpec | argparse.Namespace | dict[str, Any], *, update: bool = False) -> dict[str, Any]:
    commands = _commands_module()
    spec = adset_spec_from(source)
    payload = _load_raw_payload(spec.payload_json, spec.payload_file, label="ad set payload")

    targeting = normalize_targeting_spec(spec.targeting_json)
    frequency_control_specs = normalize_frequency_control_specs(spec.frequency_control_specs_json)
    if frequency_control_specs is None:
        frequency_control_specs = normalize_frequency_control_specs(spec.frequency_cap_json, "frequency_cap")
    promoted_object = normalize_promoted_object(spec.promoted_object_json)
    bid_constraints = parse_bid_constraints(spec.bid_constraints_json)

    _assign(payload, "campaign_id", spec.campaign_id)
    _assign(payload, "name", commands.validate_name(spec.name, "ad set name"))
    _assign(payload, "optimization_goal", spec.optimization_goal)
    _assign(payload, "billing_event", spec.billing_event)
    if not update and "status" not in payload:
        payload["status"] = spec.status or "PAUSED"
    else:
        _assign(payload, "status", spec.status)
    _assign(payload, "daily_budget", _optional_positive_int_str(spec.daily_budget, "daily_budget"))
    _assign(payload, "lifetime_budget", _optional_positive_int_str(spec.lifetime_budget, "lifetime_budget"))
    _assign(payload, "bid_amount", _optional_positive_int_str(spec.bid_amount, "bid_amount"))
    _assign(payload, "bid_strategy", spec.bid_strategy)
    _assign(payload, "start_time", commands.validate_iso_datetime(spec.start_time, "start_time"))
    _assign(payload, "end_time", commands.validate_iso_datetime(spec.end_time, "end_time"))
    _assign(payload, "destination_type", spec.destination_type)
    _assign(payload, "promoted_object", commands.json_compact(promoted_object) if promoted_object is not None else None)
    _assign(payload, "targeting", commands.json_compact(targeting) if targeting is not None else None)
    _assign(
        payload,
        "frequency_control_specs",
        commands.json_compact(frequency_control_specs) if frequency_control_specs is not None else None,
    )
    _assign(payload, "bid_constraints", commands.json_compact(bid_constraints) if bid_constraints is not None else None)

    payload = commands.filter_empty(payload)
    _compact_payload_field(payload, "targeting", label="targeting")
    _compact_payload_field(payload, "promoted_object", label="promoted_object")
    _compact_payload_field(payload, "frequency_control_specs", label="frequency_control_specs", expected_type=list)
    _compact_payload_field(payload, "bid_constraints", label="bid_constraints")

    if not update:
        payload["campaign_id"] = commands.validate_non_empty(payload.get("campaign_id"), "campaign_id")
        payload["name"] = commands.validate_non_empty(payload.get("name"), "ad set name")
        payload["optimization_goal"] = commands.validate_non_empty(payload.get("optimization_goal"), "optimization_goal")
        payload["billing_event"] = commands.validate_non_empty(payload.get("billing_event"), "billing_event")

    if payload.get("daily_budget") and payload.get("lifetime_budget"):
        raise commands.CliError("adset: cannot set both daily_budget and lifetime_budget")

    normalized_bid_constraints = (
        commands.parse_json_option(payload.get("bid_constraints"), "bid_constraints", dict)
        if payload.get("bid_constraints") not in (None, "")
        else None
    )
    validate_bid_strategy_constraints(
        payload.get("bid_strategy"),
        bid_amount=_payload_int(payload.get("bid_amount")),
        bid_constraints=normalized_bid_constraints,
        label_prefix="adset",
    )
    if payload.get("start_time") and payload.get("end_time"):
        start_dt = datetime.fromisoformat(str(payload["start_time"]).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(payload["end_time"]).replace("Z", "+00:00"))
        if end_dt <= start_dt:
            raise commands.CliError("adset: end_time must be later than start_time")

    optimization_goal = payload.get("optimization_goal")
    promoted_object_payload = (
        commands.parse_json_option(payload.get("promoted_object"), "promoted_object", dict)
        if payload.get("promoted_object") not in (None, "")
        else None
    )
    if optimization_goal == "APP_INSTALLS":
        if (
            not promoted_object_payload
            or not promoted_object_payload.get("application_id")
            or not promoted_object_payload.get("object_store_url")
        ):
            raise commands.CliError(
                "adset: promoted_object.application_id and promoted_object.object_store_url are required for APP_INSTALLS"
            )
    if update and not payload:
        raise commands.CliError("No update fields provided. Pass at least one update option.")
    return payload


def build_ad_payload(source: AdSpec | argparse.Namespace | dict[str, Any], *, update: bool = False) -> dict[str, Any]:
    commands = _commands_module()
    spec = ad_spec_from(source)
    payload = _load_raw_payload(spec.payload_json, spec.payload_file, label="ad payload")

    _assign(payload, "name", commands.validate_name(spec.name, "ad name"))
    _assign(payload, "adset_id", spec.adset_id)
    if not update and "status" not in payload:
        payload["status"] = spec.status or "PAUSED"
    else:
        _assign(payload, "status", spec.status)
    if spec.creative_id is not None:
        payload["creative"] = commands.json_compact({"creative_id": spec.creative_id})
    _assign(payload, "bid_amount", _optional_positive_int_str(spec.bid_amount, "bid_amount"))
    if spec.tracking_specs_json is not None:
        payload["tracking_specs"] = commands.json_compact(normalize_tracking_specs(spec.tracking_specs_json))
    _assign(payload, "conversion_domain", commands.validate_domain(spec.conversion_domain, "conversion_domain"))

    payload = commands.filter_empty(payload)
    _compact_payload_field(payload, "creative", label="creative")
    _compact_payload_field(payload, "tracking_specs", label="tracking_specs", expected_type=list)

    if not update:
        payload["name"] = commands.validate_non_empty(payload.get("name"), "ad name")
        payload["adset_id"] = commands.validate_non_empty(payload.get("adset_id"), "adset_id")
    if update and not payload:
        raise commands.CliError("No update fields provided. Pass at least one update option.")
    return payload


def build_story_spec(source: CreativeSpec | argparse.Namespace | dict[str, Any]) -> dict[str, Any]:
    commands = _commands_module()
    spec = creative_spec_from(source)
    if spec.object_story_spec:
        return commands.parse_json_option(spec.object_story_spec, "object_story_spec", dict) or {}
    if not spec.page_id:
        raise commands.CliError("Missing --page-id or --object-story-spec")
    story_spec: dict[str, Any] = {"page_id": spec.page_id}
    if spec.instagram_user_id:
        story_spec["instagram_user_id"] = spec.instagram_user_id
    if spec.video_id:
        call_to_action = {"type": spec.call_to_action or "LEARN_MORE"}
        if spec.link:
            call_to_action["value"] = {"link": spec.link}
        video_data: dict[str, Any] = {"video_id": spec.video_id, "call_to_action": call_to_action}
        if spec.image_hash:
            video_data["image_hash"] = spec.image_hash
        if spec.message:
            video_data["message"] = spec.message
        if spec.headline:
            video_data["title"] = spec.headline
        if spec.description:
            video_data["link_description"] = spec.description
        story_spec["video_data"] = video_data
        return story_spec

    if not spec.image_hash or not spec.link:
        raise commands.CliError("Image creative mode requires --page-id, --image-hash and --link.")
    link_data: dict[str, Any] = {"image_hash": spec.image_hash, "link": spec.link}
    if spec.message:
        link_data["message"] = spec.message
    if spec.headline:
        link_data["name"] = spec.headline
    if spec.description:
        link_data["description"] = spec.description
    if spec.call_to_action:
        call_to_action: dict[str, Any] = {"type": spec.call_to_action}
        if spec.link:
            call_to_action["value"] = {"link": spec.link}
        link_data["call_to_action"] = call_to_action
    story_spec["link_data"] = link_data
    return story_spec


def _has_creative_story_inputs(spec: CreativeSpec) -> bool:
    return any(
        value not in (None, "")
        for value in (
            spec.page_id,
            spec.instagram_user_id,
            spec.video_id,
            spec.image_hash,
            spec.link,
            spec.message,
            spec.headline,
            spec.description,
            spec.object_story_spec,
        )
    )


def build_creative_payload(
    source: CreativeSpec | argparse.Namespace | dict[str, Any],
    *,
    update: bool = False,
) -> dict[str, Any]:
    commands = _commands_module()
    spec = creative_spec_from(source)
    payload = _load_raw_payload(spec.payload_json, spec.payload_file, label="creative payload")

    _assign(payload, "name", spec.name)

    if spec.object_story_id is not None:
        payload["object_story_id"] = spec.object_story_id
        payload.pop("object_story_spec", None)
    elif spec.object_story_spec is not None or _has_creative_story_inputs(spec) or (
        not update and "object_story_id" not in payload and "object_story_spec" not in payload
    ):
        payload["object_story_spec"] = json.dumps(build_story_spec(spec), ensure_ascii=False, separators=(",", ":"))
        payload.pop("object_story_id", None)

    if spec.asset_feed_spec is not None:
        payload["asset_feed_spec"] = _normalize_json_text(spec.asset_feed_spec, "asset_feed_spec")
    if spec.media_sourcing_spec is not None:
        payload["media_sourcing_spec"] = _normalize_json_text(spec.media_sourcing_spec, "media_sourcing_spec")
    if spec.instagram_actor_id is not None:
        payload["instagram_actor_id"] = spec.instagram_actor_id
    if spec.url_tags is not None:
        payload["url_tags"] = spec.url_tags

    payload = commands.filter_empty(payload)
    _compact_payload_field(payload, "object_story_spec", label="object_story_spec")
    _compact_payload_field(payload, "asset_feed_spec", label="asset_feed_spec")
    _compact_payload_field(payload, "media_sourcing_spec", label="media_sourcing_spec")

    if not update:
        payload["name"] = commands.validate_non_empty(payload.get("name"), "creative name")
    if update and not payload:
        raise commands.CliError("No update fields provided. Pass at least one update option.")
    return payload
