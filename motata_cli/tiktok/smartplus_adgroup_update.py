"""Incremental update contract, reviewed against the 2026-11-12 official snapshot.

Only supplied fields are validated/sent. Existing state is used for conditional
checks, never copied into the outgoing patch. Unknown fields remain untouched.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from motata_cli.common.errors import CliError

CREATE_ONLY = frozenset({
    'campaign_id', 'promotion_type', 'optimization_goal', 'optimization_event', 'optimization_event_type',
    'identity_id', 'identity_type', 'identity_authorized_bc_id', 'native_series_id', 'drama_selling_setting',
    'budget_mode', 'bid_type', 'deep_bid_type', 'vbo_window', 'billing_event', 'placement_type', 'placements',
    'pacing', 'app_id', 'app_config', 'operation_status', 'video_download_disabled', 'tiktok_subplacements',
    'current_budget', 'secondary_status', 'create_time', 'modify_time',
})
BOOLEAN_FIELDS = ('automated_keywords_enabled', 'organic_comment_disabled', 'comment_disabled', 'share_disabled',
                  'is_hfss', 'is_lhf_compliance', 'suggestion_audience_enabled')
CONTEXT_FIELDS = frozenset({
    'bid_price', 'conversion_bid_price', 'roas_bid', 'budget', 'budget_auto_adjust_strategy', 'value_rule_set_id',
    'automated_keywords_enabled', 'search_keywords', 'organic_comment_disabled', 'is_hfss', 'is_lhf_compliance',
    'targeting_optimization_mode', 'suggestion_audience_enabled', 'targeting_spec',
    'schedule_type', 'schedule_start_time', 'schedule_end_time',
})
STRING_ARRAYS = (
    'location_ids', 'zipcode_ids', 'languages', 'operating_systems', 'excluded_audience_ids', 'age_groups',
    'audience_ids', 'interest_category_ids', 'interest_keyword_ids', 'purchase_intention_keyword_ids',
    'household_income', 'device_model_ids', 'network_types', 'carrier_ids', 'isp_ids', 'blocked_pangle_app_ids',
)
TARGET_FIELDS = frozenset((*STRING_ARRAYS, 'spc_audience_age', 'gender', 'shopping_ads_retargeting_type',
    'shopping_ads_retargeting_actions_days', 'included_custom_actions', 'excluded_custom_actions',
    'shopping_ads_retargeting_custom_audience_relation', 'actions', 'smart_interest_behavior_enabled',
    'smart_audience_enabled', 'spending_power', 'min_android_version', 'min_ios_version',
    'device_price_ranges', 'saved_audience_id'))


def fail(message):
    raise CliError('Upgraded Smart+ adgroup update: ' + message)


def text(value, name, *, empty=False, maximum=None):
    if not isinstance(value, str) or (not empty and not value.strip()) or (maximum and len(value) > maximum):
        fail(f'{name} must be a valid string' + (f' (max {maximum} characters).' if maximum else '.'))


def number(value, name, *, minimum=0, maximum=None, inclusive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or (value < minimum if inclusive else value <= minimum) or (maximum is not None and value > maximum)):
        fail(f'Invalid {name}.')


def enum(value, name, choices):
    if not isinstance(value, str) or value not in choices:
        fail(f'Invalid {name}; expected one of {", ".join(choices)}.')


def timestamp(value, name):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', value):
            raise ValueError()
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        fail(f'{name} requires UTC YYYY-MM-DD HH:MM:SS.')


def validate_targeting(target):
    if not isinstance(target, dict):
        fail('targeting_spec must be an object.')
    for key in STRING_ARRAYS:
        if key in target:
            if not isinstance(target[key], list):
                fail(f'targeting_spec.{key} must be a string array.')
            for value in target[key]:
                text(value, f'targeting_spec.{key}')
    if len(target.get('location_ids', [])) + len(target.get('zipcode_ids', [])) > 3000:
        fail('Combined location/zip/postal IDs must not exceed 3000.')
    if target.get('device_model_ids') and target.get('device_price_ranges'):
        fail('device_model_ids and device_price_ranges are mutually exclusive.')
    for key in ('smart_interest_behavior_enabled', 'smart_audience_enabled'):
        if key in target and not isinstance(target[key], bool):
            fail(f'{key} must be boolean.')
    for key, choices in {
        'spc_audience_age': ('ALL', 'OVER_EIGHTEEN', 'OVER_TWENTY_FIVE'),
        'gender': ('GENDER_FEMALE', 'GENDER_MALE', 'GENDER_UNLIMITED'),
        'spending_power': ('ALL', 'HIGH'),
        'shopping_ads_retargeting_type': ('OFF', 'LAB1', 'LAB2', 'LAB3'),
        'shopping_ads_retargeting_custom_audience_relation': ('OR', 'AND'),
    }.items():
        if key in target:
            enum(target[key], key, choices)
    for key in ('min_android_version', 'min_ios_version', 'saved_audience_id'):
        if key in target:
            text(target[key], key)
    if 'device_price_ranges' in target:
        values = target['device_price_ranges']
        if not isinstance(values, list) or len(values) != 2:
            fail('device_price_ranges requires lower and upper values.')
        for value in values:
            number(value, 'device_price_ranges', inclusive=True, maximum=10000)
            if value % 50:
                fail('device_price_ranges values must be multiples of 50.')
        if values[0] > values[1]:
            fail('device_price_ranges must be in ascending order.')
    if 'operating_systems' in target:
        if len(target['operating_systems']) > 1:
            fail('Only one operating_systems value is allowed.')
        for value in target['operating_systems']:
            enum(value, 'operating_systems', ('ANDROID', 'IOS'))
    for key, choices in {
        'household_income': ('TOP5', 'TOP10', 'TOP10_25', 'TOP25_50'),
        'age_groups': ('AGE_13_17', 'AGE_18_24', 'AGE_25_34', 'AGE_35_44', 'AGE_45_54', 'AGE_55_100'),
    }.items():
        for value in target.get(key, []):
            enum(value, key, choices)
    if 'shopping_ads_retargeting_actions_days' in target:
        value = target['shopping_ads_retargeting_actions_days']
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value not in (1, 2, 3, 7, 14, 30, 60, 90, 180):
            fail('Invalid shopping_ads_retargeting_actions_days.')
    for key in ('included_custom_actions', 'excluded_custom_actions', 'actions'):
        if key not in target:
            continue
        if not isinstance(target[key], list) or any(not isinstance(item, dict) for item in target[key]):
            fail(f'{key} must be an object array.')
        for item in target[key]:
            if key != 'actions':
                enum(item.get('code'), key + '.code', ('VIEW_PRODUCT', 'ADD_TO_CART', 'PURCHASE'))
                value = item.get('days')
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 180:
                    fail(f'{key}.days must be an integer from 1 to 180.')
            else:
                scene = item.get('action_scene')
                enum(scene, 'action_scene', ('VIDEO_RELATED', 'CREATOR_RELATED', 'HASHTAG_RELATED'))
                if 'action_period' in item and (isinstance(item['action_period'], bool) or item['action_period'] not in (0, 7, 15)):
                    fail('Invalid action_period.')
                choices = {'VIDEO_RELATED': ('WATCHED_TO_END', 'LIKED', 'COMMENTED', 'SHARED'),
                           'CREATOR_RELATED': ('FOLLOWING', 'VIEW_HOMEPAGE'), 'HASHTAG_RELATED': ('VIEW_HASHTAG',)}[scene]
                for field in ('video_user_actions', 'action_category_ids'):
                    if field in item:
                        if not isinstance(item[field], list):
                            fail(f'{field} must be a string array.')
                        for value in item[field]:
                            text(value, field)
                            if field == 'video_user_actions':
                                enum(value, field, choices)


def validate_update(payload, *, now=None):
    if not isinstance(payload, dict):
        fail('payload must be an object.')
    invalid = CREATE_ONLY.intersection(payload)
    if invalid:
        fail('create-only/response-only fields are not writable: ' + ', '.join(sorted(invalid)))
    misplaced = TARGET_FIELDS.intersection(payload)
    if misplaced:
        fail('Place these fields inside targeting_spec: ' + ', '.join(sorted(misplaced)))
    for key in ('advertiser_id', 'adgroup_id'):
        text(payload.get(key), key)
    if 'adgroup_name' in payload:
        text(payload['adgroup_name'], 'adgroup_name', maximum=512)
    if 'value_rule_set_id' in payload:
        text(payload['value_rule_set_id'], 'value_rule_set_id')
    for key in BOOLEAN_FIELDS:
        if key in payload and not isinstance(payload[key], bool):
            fail(f'{key} must be boolean.')
    for key in ('bid_price', 'conversion_bid_price', 'budget'):
        if key in payload:
            number(payload[key], key)
    if 'roas_bid' in payload:
        number(payload['roas_bid'], 'roas_bid', minimum=0.01, maximum=1000, inclusive=True)
    for key, choices in {
        'targeting_optimization_mode': ('AUTOMATIC', 'MANUAL'),
        'budget_auto_adjust_strategy': ('AUTO_BUDGET_INCREASE', 'UNSET'),
        'schedule_type': ('SCHEDULE_FROM_NOW', 'SCHEDULE_START_END'),
    }.items():
        if key in payload:
            enum(payload[key], key, choices)
    if 'dayparting' in payload and (not isinstance(payload['dayparting'], str) or not re.fullmatch('[01]{336}', payload['dayparting'])):
        fail('dayparting must contain exactly 336 binary characters.')
    now = now or datetime.now(timezone.utc)
    if 'schedule_start_time' in payload:
        start = timestamp(payload['schedule_start_time'], 'schedule_start_time')
        if start < now - timedelta(hours=12) or start > datetime(2028, 1, 1, tzinfo=timezone.utc):
            fail('schedule_start_time must be within the past 12 hours or later, and no later than 2028-01-01 UTC.')
    if 'schedule_end_time' in payload:
        if timestamp(payload['schedule_end_time'], 'schedule_end_time') > datetime(2038, 1, 1, tzinfo=timezone.utc):
            fail('schedule_end_time must not be later than 2038-01-01 UTC.')
    if 'targeting_spec' in payload:
        validate_targeting(payload['targeting_spec'])
    if 'search_keywords' in payload:
        values = payload['search_keywords']
        if not isinstance(values, list) or not 1 <= len(values) <= 1000:
            fail('search_keywords must contain 1–1000 objects.')
        for value in values:
            if not isinstance(value, dict):
                fail('search_keywords entries must be objects.')
            text(value.get('keyword'), 'keyword', maximum=80)
            if any(char in value['keyword'] for char in '!#$%&()*-/:;<>?@\\^_¥…'):
                fail('keyword contains unsupported punctuation.')
            enum(value.get('match_type'), 'match_type', ('PRECISE_WORD', 'PHRASE_WORD', 'BROAD_WORD'))


def validate_context(payload, group, campaign):
    """Validate only rules touched by the patch, using the current remote state."""
    effective = {**group, **payload}
    cbo = campaign.get('budget_optimize_on', True)
    if {'bid_price', 'conversion_bid_price', 'budget', 'budget_auto_adjust_strategy'} & payload.keys() and not isinstance(cbo, bool):
        fail('Cannot establish the current campaign budget strategy.')
    for key in ('bid_price', 'conversion_bid_price'):
        if key in payload:
            goals = ('CLICK',) if key == 'bid_price' else ('CONVERT', 'TRAFFIC_LANDING_PAGE_VIEW', 'INSTALL', 'IN_APP_EVENT')
            if group.get('optimization_goal') not in goals or group.get('bid_type') != 'BID_TYPE_CUSTOM':
                fail(f'{key} is incompatible with the current goal/bid type.')
            budget = campaign.get('budget') if cbo else effective.get('budget')
            if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or payload[key] >= budget:
                fail(f'{key} must be lower than the effective budget.')
    if 'roas_bid' in payload and group.get('deep_bid_type') != 'VO_MIN_ROAS':
        fail('roas_bid requires existing deep_bid_type VO_MIN_ROAS; it cannot change the bid strategy.')
    if {'budget', 'budget_auto_adjust_strategy'} & payload.keys() and cbo:
        fail('CBO budgets must be updated at campaign level.')
    if 'budget_auto_adjust_strategy' in payload:
        app = campaign.get('objective_type') == 'APP_PROMOTION' and all(group.get(k) == v for k, v in
            {'optimization_goal': 'VALUE', 'deep_bid_type': 'VO_MIN_ROAS', 'vbo_window': 'ZERO_DAY'}.items())
        lead = campaign.get('objective_type') == 'LEAD_GENERATION' and group.get('bid_type') == 'BID_TYPE_CUSTOM'
        if group.get('budget_mode') != 'BUDGET_MODE_DYNAMIC_DAILY_BUDGET' or not (app or lead):
            fail('budget_auto_adjust_strategy requires an eligible daily-budget app/lead group.')
    if 'value_rule_set_id' in payload and campaign.get('special_industries'):
        fail('value_rule_set_id is incompatible with special_industries.')
    if {'automated_keywords_enabled', 'search_keywords'} & payload.keys() and campaign.get('is_search_campaign') is not True:
        fail('Search keyword fields require an existing Search campaign.')
    placements = group.get('placements') or []
    has_tiktok = group.get('placement_type') == 'PLACEMENT_TYPE_AUTOMATIC' or (
        group.get('placement_type') == 'PLACEMENT_TYPE_NORMAL' and 'PLACEMENT_TIKTOK' in placements)
    if 'organic_comment_disabled' in payload and not has_tiktok:
        fail('organic_comment_disabled requires TikTok placement.')
    if 'is_hfss' in payload and effective.get('targeting_optimization_mode') != 'MANUAL':
        fail('is_hfss requires MANUAL targeting.')
    if 'suggestion_audience_enabled' in payload and effective.get('targeting_optimization_mode') != 'AUTOMATIC':
        fail('suggestion_audience_enabled requires AUTOMATIC targeting.')
    if payload.get('suggestion_audience_enabled') is True and campaign.get('rta_id'):
        fail('Audience suggestions require AUTOMATIC targeting with RTA disabled.')
    if {'targeting_optimization_mode', 'targeting_spec'} & payload.keys() and group.get('promotion_type') == 'NATIVE_SERIES':
        if effective.get('targeting_optimization_mode', 'AUTOMATIC') != 'AUTOMATIC':
            fail('Drama Series supports AUTOMATIC targeting.')
        age = (payload.get('targeting_spec') or {}).get('spc_audience_age')
        if age is not None and age not in ('OVER_EIGHTEEN', 'OVER_TWENTY_FIVE'):
            fail('Drama Series minimum age must be 18 or 25.')
    target_patch = payload.get('targeting_spec') or {}
    target = {**(group.get('targeting_spec') or {}), **target_patch}
    validate_targeting(target_patch)
    if target_patch:
        if target_patch.get('saved_audience_id') and (not has_tiktok or
                set(campaign.get('special_industries') or []) & {'HOUSING', 'EMPLOYMENT', 'CREDIT'}):
            fail('Saved audiences require TikTok placement and no Housing/Employment/Credit category.')
        if target_patch.get('purchase_intention_keyword_ids') and not (has_tiktok or 'PLACEMENT_PANGLE' in placements):
            fail('Purchase intention targeting requires TikTok or Pangle placement.')
        if any(item.get('action_category_ids') for item in target_patch.get('actions', [])) and (
                group.get('placement_type') != 'PLACEMENT_TYPE_NORMAL' or set(placements) != {'PLACEMENT_TIKTOK'}):
            fail('Action category targeting requires TikTok-only placement.')
        if target_patch.get('isp_ids') and (not target.get('location_ids') or set(placements) == {'PLACEMENT_GLOBAL_APP_BUNDLE'}):
            fail('ISP targeting requires a country/region location and cannot use Global App Bundle alone.')
        if target.get('device_model_ids') and target.get('device_price_ranges'):
            fail('Clear the existing device model/price selection before setting the conflicting option.')
        if target_patch.get('spc_audience_age') == 'ALL' and campaign.get('smart_plus_adgroup_mode') != 'MULTIPLE':
            fail('ALL ages requires smart_plus_adgroup_mode MULTIPLE and platform allowlisting.')
        if 'AGE_13_17' in target_patch.get('age_groups', []) and not (campaign.get('objective_type') == 'APP_PROMOTION' and campaign.get('app_promotion_type') in ('APP_INSTALL', 'APP_RETARGETING')):
            fail('AGE_13_17 is limited to app install/retargeting campaigns.')
        if campaign.get('rta_id') and {'audience_ids', 'excluded_audience_ids'} & target_patch.keys():
            fail('Audience ID targeting is unsupported with RTA.')
        if target_patch.get('zipcode_ids') and (campaign.get('special_industries') or campaign.get('objective_type') == 'RF_REACH'):
            fail('Zip/postal targeting is unavailable for this campaign category/objective.')
        if any(key.startswith('shopping_ads_retargeting') or key in ('included_custom_actions', 'excluded_custom_actions') for key in target_patch):
            if not (campaign.get('objective_type') == 'WEB_CONVERSION' and campaign.get('sales_destination') in ('WEB_AND_APP', 'WEBSITE', 'APP') and campaign.get('catalog_type') == 'ECOMMERCE' and effective.get('targeting_optimization_mode') == 'MANUAL'):
                fail('Catalog retargeting requires an eligible manual ecommerce campaign.')
            kind = target.get('shopping_ads_retargeting_type')
            if kind in ('LAB1', 'LAB2') and not target.get('shopping_ads_retargeting_actions_days'):
                fail('LAB1/LAB2 requires shopping_ads_retargeting_actions_days.')
            if kind == 'LAB3' and not (target.get('included_custom_actions') or target.get('excluded_custom_actions')):
                fail('LAB3 requires included or excluded custom actions.')
            if 'shopping_ads_retargeting_custom_audience_relation' in target_patch and not (kind in ('LAB1', 'LAB2', 'LAB3') and target.get('audience_ids')):
                fail('Retargeting audience relation requires retargeting and audience_ids.')
    if {'schedule_type', 'schedule_start_time', 'schedule_end_time'} & payload.keys():
        schedule = effective.get('schedule_type')
        enum(schedule, 'effective schedule_type', ('SCHEDULE_FROM_NOW', 'SCHEDULE_START_END'))
        start = timestamp(effective.get('schedule_start_time'), 'effective schedule_start_time')
        if schedule == 'SCHEDULE_START_END':
            # The UPDATE guide limits this choice to lifetime budgets. Creation
            # has a different rule, so do not share its schedule validator.
            if ('schedule_type' in payload and group.get('budget_mode') != 'BUDGET_MODE_TOTAL'
                    and campaign.get('budget_mode') != 'BUDGET_MODE_TOTAL'):
                fail('Updating SCHEDULE_START_END requires a lifetime campaign or adgroup budget.')
            if timestamp(effective.get('schedule_end_time'), 'effective schedule_end_time') <= start:
                fail('schedule_end_time must be later than schedule_start_time.')
        elif 'schedule_end_time' in payload:
            fail('schedule_end_time is only valid for SCHEDULE_START_END.')
