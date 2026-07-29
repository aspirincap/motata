from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any


def _read(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


@dataclass(slots=True)
class CampaignSpec:
    name: str | None = None
    objective: str | None = None
    status: str | None = None
    daily_budget: str | None = None
    lifetime_budget: str | None = None
    bid_strategy: str | None = None
    bid_cap: str | None = None
    bid_constraints_json: str | None = None
    special_ad_categories: list[str] = field(default_factory=list)
    use_adset_level_budgets: bool = False
    payload_json: str | None = None
    payload_file: str | None = None


@dataclass(slots=True)
class AdSetSpec:
    campaign_id: str | None = None
    name: str | None = None
    optimization_goal: str | None = None
    billing_event: str | None = None
    status: str | None = None
    targeting_json: str | None = None
    promoted_object_json: str | None = None
    daily_budget: str | None = None
    lifetime_budget: str | None = None
    bid_amount: str | None = None
    bid_strategy: str | None = None
    bid_constraints_json: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    destination_type: str | None = None
    frequency_cap_json: str | None = None
    frequency_control_specs_json: str | None = None
    payload_json: str | None = None
    payload_file: str | None = None


@dataclass(slots=True)
class AdSpec:
    name: str | None = None
    adset_id: str | None = None
    creative_id: str | None = None
    status: str | None = None
    bid_amount: str | None = None
    tracking_specs_json: str | None = None
    conversion_domain: str | None = None
    payload_json: str | None = None
    payload_file: str | None = None


@dataclass(slots=True)
class CreativeSpec:
    name: str | None = None
    page_id: str | None = None
    instagram_user_id: str | None = None
    instagram_actor_id: str | None = None
    video_id: str | None = None
    image_hash: str | None = None
    link: str | None = None
    message: str | None = None
    headline: str | None = None
    description: str | None = None
    call_to_action: str | None = None
    object_story_spec: str | None = None
    object_story_id: str | None = None
    asset_feed_spec: str | None = None
    media_sourcing_spec: str | None = None
    url_tags: str | None = None
    payload_json: str | None = None
    payload_file: str | None = None


def campaign_spec_from(source: CampaignSpec | argparse.Namespace | dict[str, Any]) -> CampaignSpec:
    if isinstance(source, CampaignSpec):
        return source
    return CampaignSpec(
        name=_read(source, "name"),
        objective=_read(source, "objective"),
        status=_read(source, "status"),
        daily_budget=_read(source, "daily_budget"),
        lifetime_budget=_read(source, "lifetime_budget"),
        bid_strategy=_read(source, "bid_strategy"),
        bid_cap=_read(source, "bid_cap"),
        bid_constraints_json=_read(source, "bid_constraints_json"),
        special_ad_categories=list(_read(source, "special_ad_categories", []) or []),
        use_adset_level_budgets=bool(_read(source, "use_adset_level_budgets", False)),
        payload_json=_read(source, "payload_json"),
        payload_file=_read(source, "payload_file"),
    )


def adset_spec_from(source: AdSetSpec | argparse.Namespace | dict[str, Any]) -> AdSetSpec:
    if isinstance(source, AdSetSpec):
        return source
    return AdSetSpec(
        campaign_id=_read(source, "campaign_id"),
        name=_read(source, "name"),
        optimization_goal=_read(source, "optimization_goal"),
        billing_event=_read(source, "billing_event"),
        status=_read(source, "status"),
        targeting_json=_read(source, "targeting_json"),
        promoted_object_json=_read(source, "promoted_object_json"),
        daily_budget=_read(source, "daily_budget"),
        lifetime_budget=_read(source, "lifetime_budget"),
        bid_amount=_read(source, "bid_amount"),
        bid_strategy=_read(source, "bid_strategy"),
        bid_constraints_json=_read(source, "bid_constraints_json"),
        start_time=_read(source, "start_time"),
        end_time=_read(source, "end_time"),
        destination_type=_read(source, "destination_type"),
        frequency_cap_json=_read(source, "frequency_cap_json"),
        frequency_control_specs_json=_read(source, "frequency_control_specs_json"),
        payload_json=_read(source, "payload_json"),
        payload_file=_read(source, "payload_file"),
    )


def ad_spec_from(source: AdSpec | argparse.Namespace | dict[str, Any]) -> AdSpec:
    if isinstance(source, AdSpec):
        return source
    return AdSpec(
        name=_read(source, "name"),
        adset_id=_read(source, "adset_id"),
        creative_id=_read(source, "creative_id"),
        status=_read(source, "status"),
        bid_amount=_read(source, "bid_amount"),
        tracking_specs_json=_read(source, "tracking_specs_json"),
        conversion_domain=_read(source, "conversion_domain"),
        payload_json=_read(source, "payload_json"),
        payload_file=_read(source, "payload_file"),
    )


def creative_spec_from(source: CreativeSpec | argparse.Namespace | dict[str, Any]) -> CreativeSpec:
    if isinstance(source, CreativeSpec):
        return source
    return CreativeSpec(
        name=_read(source, "name"),
        page_id=_read(source, "page_id"),
        instagram_user_id=_read(source, "instagram_user_id"),
        instagram_actor_id=_read(source, "instagram_actor_id"),
        video_id=_read(source, "video_id"),
        image_hash=_read(source, "image_hash"),
        link=_read(source, "link"),
        message=_read(source, "message"),
        headline=_read(source, "headline"),
        description=_read(source, "description"),
        call_to_action=_read(source, "call_to_action"),
        object_story_spec=_read(source, "object_story_spec"),
        object_story_id=_read(source, "object_story_id"),
        asset_feed_spec=_read(source, "asset_feed_spec"),
        media_sourcing_spec=_read(source, "media_sourcing_spec"),
        url_tags=_read(source, "url_tags"),
        payload_json=_read(source, "payload_json"),
        payload_file=_read(source, "payload_file"),
    )
