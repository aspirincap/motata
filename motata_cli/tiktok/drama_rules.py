"""Reviewed Drama constraints, separate from general forward-compatible payloads.

Source (reviewed 2026-11-12): /portal/docs/
create-an-upgraded-smart-mini-series-feed-campaign/v1.3
and update-an-upgraded-smart-ad-group/v1.3.
"""
from __future__ import annotations

import math
from datetime import datetime

from motata_cli.common.errors import CliError

DAILY = "BUDGET_MODE_DYNAMIC_DAILY_BUDGET"
TOTAL = "BUDGET_MODE_TOTAL"
INFINITE = "BUDGET_MODE_INFINITE"
IDENTITY_FIELDS = ("identity_type", "identity_id", "identity_authorized_bc_id")
# Kept as a compatibility import; update validation has its own contract.
from .smartplus_adgroup_update import validate_update


def _number(value, label, minimum=0, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value <= minimum or (maximum is not None and value > maximum)):
        raise CliError(f"Invalid {label}.")


def validate_budget_and_bidding(campaign, group):
    cbo = campaign.get("budget_optimize_on", True)
    if not isinstance(cbo, bool):
        raise CliError("budget_optimize_on must be boolean.")
    mode = campaign.get("budget_mode")
    allowed = (DAILY, TOTAL) if cbo else (INFINITE, "BUDGET_MODE_DAY", TOTAL)
    if mode not in allowed:
        raise CliError("Invalid campaign.budget_mode for its budget_optimize_on strategy.")
    if mode != INFINITE:
        _number(campaign.get("budget"), "campaign budget")
    elif "budget" in campaign:
        raise CliError("An unlimited campaign must omit budget.")
    if cbo:
        if "budget" in group or "budget_mode" in group:
            raise CliError("CBO uses the campaign budget; omit adgroup budget and budget_mode.")
        effective_mode = mode
    else:
        effective_mode = group.get("budget_mode")
        if effective_mode not in ((DAILY,) if mode == "BUDGET_MODE_DAY" else (TOTAL, DAILY)):
            raise CliError("Invalid adgroup.budget_mode for the campaign budget limit.")
        _number(group.get("budget"), "adgroup budget")
    strategy = group.get("deep_bid_type")
    if strategy not in ("VO_MIN_ROAS", "VO_HIGHEST_VALUE"):
        raise CliError("Specify deep_bid_type VO_MIN_ROAS or VO_HIGHEST_VALUE.")
    if any(key in group for key in ("bid_price", "conversion_bid_price", "click_attribution_window")):
        raise CliError("Drama groups must omit bid_price, conversion_bid_price and click_attribution_window.")
    if strategy == "VO_MIN_ROAS":
        if effective_mode != DAILY:
            raise CliError("Target ROAS requires a dynamic daily campaign or adgroup budget.")
        _number(group.get("roas_bid"), "roas_bid (0.01–1000)", maximum=1000)
        if group["roas_bid"] < 0.01:
            raise CliError("roas_bid must be at least 0.01.")
    elif "roas_bid" in group:
        raise CliError("roas_bid is only valid with VO_MIN_ROAS.")
    schedule = group.get("schedule_type")
    if schedule not in ("SCHEDULE_FROM_NOW", "SCHEDULE_START_END"):
        raise CliError("Specify schedule_type SCHEDULE_FROM_NOW or SCHEDULE_START_END.")
    try:
        start = datetime.strptime(group.get("schedule_start_time", ""), "%Y-%m-%d %H:%M:%S")
        if schedule == "SCHEDULE_START_END":
            end = datetime.strptime(group.get("schedule_end_time", ""), "%Y-%m-%d %H:%M:%S")
            if end <= start:
                raise ValueError("end precedes start")
        elif effective_mode != DAILY or "schedule_end_time" in group:
            raise ValueError("continuous schedule requires daily budget and no end")
    except (TypeError, ValueError):
        raise CliError("Invalid Drama schedule; use UTC YYYY-MM-DD HH:MM:SS and a budget-compatible time range.") from None


def validate_shared_settings(campaign, groups):
    """Also usable for copy plans with multiple groups; do not treat response budgets as input."""
    shared = ["bid_type", "deep_bid_type", "budget_mode"]
    if campaign.get("budget_optimize_on", True):
        shared += ["promotion_type", "optimization_goal", "optimization_event",
                   "roas_bid", "vbo_window", "billing_event"]
        owned = [row for row in groups if not row.get("drama_selling_setting")]
        for key in (*IDENTITY_FIELDS, "native_series_id"):
            if len({str(row.get(key)) for row in owned}) > 1:
                raise CliError(f"CBO ad groups must share {key}.")
    for key in shared:
        values = [row[key] for row in groups if key in row and row[key] is not None]
        if values and any(value != values[0] for value in values[1:]):
            raise CliError(f"Drama ad groups must share {key} under this campaign strategy.")


def validate_assets(group, ad):
    targeting = group.get("targeting_spec")
    if not isinstance(targeting, dict) or not (targeting.get("location_ids") or targeting.get("zipcode_ids")):
        raise CliError("Provide location_ids and/or zipcode_ids within adgroup.targeting_spec.")
    for key in ("location_ids", "zipcode_ids"):
        if key in group:
            raise CliError(f"Place {key} inside targeting_spec for Upgraded Smart+.")
        if key in targeting and (not isinstance(targeting[key], list) or any(not isinstance(value, str) or not value for value in targeting[key])):
            raise CliError(f"targeting_spec.{key} must be a list of string IDs.")
    if 'spc_audience_age' in targeting and targeting['spc_audience_age'] not in ('OVER_EIGHTEEN', 'OVER_TWENTY_FIVE'):
        raise CliError('Drama Series minimum age must be 18 or 25.')
    from .smartplus_adgroup_update import validate_targeting
    validate_targeting(targeting)
    if 'tiktok_subplacements' in group:
        values = group['tiktok_subplacements']
        if not isinstance(values, list) or any(value not in ('LEMON8', 'PINE_DRAMA') for value in values):
            raise CliError('Drama tiktok_subplacements supports LEMON8 and PINE_DRAMA.')
    for key in ('comment_disabled', 'share_disabled', 'video_download_disabled'):
        if key in group and not isinstance(group[key], bool):
            raise CliError(f'{key} must be boolean.')
    if 'dayparting' in group:
        import re
        if not isinstance(group['dayparting'], str) or not re.fullmatch('[01]{336}', group['dayparting']):
            raise CliError('dayparting must contain exactly 336 binary characters.')
    if len(ad["creative_list"]) > 50:
        raise CliError("Drama supports at most 50 creatives per ad.")
    config = ad.get("ad_configuration")
    if not isinstance(config, dict) or not config.get("call_to_action_id"):
        raise CliError("Drama requires ad.ad_configuration.call_to_action_id for a dynamic CTA.")
    for key in ("call_to_action_list", "landing_page_url_list", "interactive_add_on_list"):
        if key in ad or key in config:
            raise CliError(f"Drama ads must omit {key}.")
    for creative in ad["creative_list"]:
        info = creative.get("creative_info")
        if not isinstance(info, dict) or not info.get("ad_format"):
            raise CliError("Each creative_info requires ad_format and media or TikTok post input.")
        if not any(info.get(key) for key in ("video_info", "image_info", "tiktok_item_id")):
            raise CliError("Each creative_info requires video_info, image_info or tiktok_item_id.")
        validate_identity(info, 'creative_info')


def validate_identity(body, label):
    if body.get('identity_type') not in ('TT_USER', 'BC_AUTH_TT'):
        raise CliError(f'{label}.identity_type must be TT_USER or BC_AUTH_TT.')
    if not isinstance(body.get('identity_id'), str) or not body['identity_id'].strip():
        raise CliError(f'{label}.identity_id must be a nonempty string.')
    if body['identity_type'] == 'BC_AUTH_TT' and (
            not isinstance(body.get('identity_authorized_bc_id'), str) or not body['identity_authorized_bc_id'].strip()):
        raise CliError(f'{label}: BC_AUTH_TT requires identity_authorized_bc_id.')
