"""Explicit reviewed endpoint set, not a universal authenticated proxy.

Expansion requires fixtures, ownership analysis, and response-safety coverage.
Unsupported current CLI operations remain visible as incomplete in the inventory.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from .errors import GatewayError
from .protocol import PlatformRequest

@dataclass(frozen=True)
class Endpoint:
    name: str
    scope: str
    write: bool = False
    object_type: str | None = None
    result_key: str | None = None
    update: bool = False

META_COLLECTIONS = {'campaigns': 'campaign', 'adsets': 'adset', 'ads': 'ad',
                    'adcreatives': 'creative', 'adimages': 'image', 'advideos': 'video',
                    'adspixels': 'pixel', 'insights': None}
TIKTOK_COLLECTIONS = {'campaign/get/': 'campaign', 'adgroup/get/': 'adgroup',
                      'ad/get/': 'ad', 'report/integrated/get/': None,
                      'smart_plus/campaign/get/': 'campaign', 'smart_plus/adgroup/get/': 'adgroup',
                      'smart_plus/ad/get/': 'ad', 'gmv_max/campaign/get/': 'campaign',
                      'campaign/gmv_max/info/': None, 'gmv_max/report/get/': None,
                      'smart_plus/material_report/overview/': None,
                      'smart_plus/material_report/breakdown/': None,
                      'pixel/list/': 'pixel', 'offline/get/': None,
                      'app/list/': None, 'app/info/': None, 'app/optimization_event/': None,
                      'identity/get/': None, 'gmv_max/store/list/': None,
                      'file/video/ad/search/': 'video', 'page/get/': None,
                      'creative/portfolio/list/': None, 'creative/portfolio/get/': None,
                      'tool/targeting/list/': None, 'tool/url_validate/': None, 'tool/vbo_status/': None}
CONTROL = {'method', 'http_method', 'method_override', 'batch', 'relative_url', 'headers', 'base_url', 'ids', 'node_ids', 'include_headers'}
CREATE_FIELDS = {'name', 'objective', 'status', 'daily_budget', 'lifetime_budget', 'bid_strategy',
                 'special_ad_categories', 'buying_type', 'spend_cap'}
META_WRITE_FIELDS = {
    'campaigns': CREATE_FIELDS,
    'adsets': {'name', 'campaign_id', 'status', 'daily_budget', 'lifetime_budget', 'bid_amount',
               'bid_strategy', 'billing_event', 'optimization_goal', 'targeting', 'promoted_object',
               'start_time', 'end_time', 'attribution_spec', 'destination_type', 'pacing_type',
               'is_dynamic_creative', 'optimization_sub_event', 'bid_constraints'},
    'ads': {'name', 'adset_id', 'creative', 'status', 'tracking_specs', 'conversion_specs'},
    'adcreatives': {'name', 'object_story_spec', 'object_story_id', 'asset_feed_spec', 'url_tags',
                    'degrees_of_freedom_spec', 'instagram_actor_id', 'contextual_multi_ads',
                    'authorization_category', 'use_page_actor_override'},
}


def validate_fields(fields):
    """Only flat projections. Nested Graph traversal needs a separate relation policy."""
    if fields is None:
        return
    text = ','.join(fields) if isinstance(fields, list) and all(isinstance(x, str) for x in fields) else str(fields)
    if any(c in text for c in '{}():%') or '.as' in text.lower():
        raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Graph field expansion requires an ownership-reviewed adapter.')


def select(request: PlatformRequest) -> Endpoint:
    if request.platform == 'meta':
        validate_fields(request.query.get('fields'))
        validate_fields((request.body or {}).get('fields'))
    if CONTROL.intersection(request.query) or CONTROL.intersection(request.body or {}):
        raise GatewayError('INVALID_REQUEST', 400, 'Transport overrides and batch tunneling are forbidden.')
    if request.platform == 'meta':
        if request.method == 'GET' and request.path == f'act_{request.account_id}':
            return Endpoint('meta.account.get', 'meta:read')
        match = re.fullmatch(r'act_([0-9]+)/([a-z]+)', request.path)
        if match and match[1] != request.account_id:
            raise GatewayError('FORBIDDEN', 403, 'Path account does not match authorization context.')
        if match and request.method == 'GET' and match[2] in META_COLLECTIONS:
            return Endpoint(f'meta.{match[2]}.list', 'meta:read', object_type=META_COLLECTIONS[match[2]])
        if match and request.method == 'POST' and match[2] in META_WRITE_FIELDS:
            if request.query or not request.body or set(request.body) - META_WRITE_FIELDS[match[2]]:
                raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'This campaign payload is not covered by the gateway policy.')
            return Endpoint(f'meta.{match[2]}.create', 'meta:write', True, META_COLLECTIONS[match[2]], 'id')
        if match and request.method == 'POST' and match[2] == 'insights':
            return Endpoint('meta.report.submit', 'meta:read', True, 'report', 'report_run_id')
        if match and request.method == 'POST' and match[2] in ('adimages', 'advideos'):
            fields = {'name', 'title'}
            if set(request.body or {}) - fields or request.query:
                raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Unreviewed media upload options.')
            return Endpoint('meta.media.upload', 'meta:upload', True, META_COLLECTIONS[match[2]], 'id')
        child = re.fullmatch(r'([0-9]+)/(insights|adsets|ads)', request.path)
        if child and request.method == 'GET':
            return Endpoint('meta.object.' + child[2], 'meta:read', object_type=META_COLLECTIONS[child[2]])
        if re.fullmatch(r'[0-9]+', request.path) and request.method in ('POST', 'DELETE'):
            allowed = {'name', 'status', 'daily_budget', 'lifetime_budget', 'bid_amount', 'bid_strategy', 'end_time'}
            if request.query or set(request.body or {}) - allowed:
                raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Unreviewed object mutation fields.')
            return Endpoint('meta.object.update', 'meta:write', True, update=True)
        if request.method == 'GET' and re.fullmatch(r'[0-9]+', request.path):
            return Endpoint('meta.object.get', 'meta:read')
    if request.platform == 'tiktok' and request.method == 'POST':
        match = re.fullmatch(r'(?:smart_plus/)?(campaign|adgroup|ad)/(create|update|status/update)/', request.path)
        upload = request.path in ('file/image/ad/upload/', 'file/video/ad/upload/')
        if match or upload:
            if request.query or str((request.body or {}).get('advertiser_id', '')) != request.account_id:
                raise GatewayError('FORBIDDEN', 403, 'Advertiser does not match authorization context.')
            reject_multi_account(request.body or {})
            if upload:
                return Endpoint('tiktok.media.upload', 'tiktok:upload', True, 'video' if '/video/' in request.path else 'image')
            return Endpoint('tiktok.' + request.path.replace('/', '.').strip('.'), 'tiktok:write', True,
                            match[1], match[1] + '_id', update=match[2] != 'create')
    if request.platform == 'tiktok' and request.method == 'GET' and request.path in TIKTOK_COLLECTIONS:
        # Multi-advertiser/biz-center report selectors are not covered by a single-account grant.
        reject_multi_account(request.query)
        # Query/body cannot override the selected advertiser, including serialized values.
        if str(request.query.get('advertiser_id', '')) != request.account_id:
            raise GatewayError('FORBIDDEN', 403, 'Advertiser does not match authorization context.')
        return Endpoint('tiktok.' + request.path.replace('/', '.').strip('.'), 'tiktok:read',
                        object_type=TIKTOK_COLLECTIONS[request.path])
    raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'This endpoint is not yet available in gateway mode.')


def reject_multi_account(values):
    if any(values.get(k) not in (None, False, '', [], {}) for k in
           ('advertiser_ids', 'bc_id', 'multi_adv_report_in_utc_time')):
        raise GatewayError('FORBIDDEN', 403, 'Multi-account selectors require a separately reviewed adapter.')


def validate_scope(principal, endpoint):
    if not {'gateway:use', endpoint.scope}.issubset(principal.scopes):
        raise GatewayError('INSUFFICIENT_SCOPE', 403, 'The access token does not permit this operation.')


def check_objects(request: PlatformRequest, state):
    if request.platform == 'meta' and re.fullmatch(r'[0-9]+(?:/[a-z]+)?', request.path):
        state.require_object('meta', request.path.split('/')[0], request.account_id)
    def references(value):
        if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
            try:
                value = json.loads(value)
            except ValueError:
                raise GatewayError('INVALID_REQUEST', 400, 'Invalid relationship JSON.') from None
        if isinstance(value, list):
            for child in value:
                references(child)
        if not isinstance(value, dict):
            return
        kinds = {'campaign_id': 'campaign', 'adset_id': 'adset', 'adgroup_id': 'adgroup',
                 'ad_id': 'ad', 'creative_id': 'creative', 'pixel_id': 'pixel', 'video_id': 'video'}
        for key, child in value.items():
            singular = key[:-1] if key.endswith('_ids') else key
            if singular in kinds and child not in (None, ''):
                for object_id in child if isinstance(child, list) else [child]:
                    state.require_object(request.platform, str(object_id), request.account_id, kinds[singular])
            else:
                references(child)
    if request.method != 'GET':
        references(request.body)
    # TikTok owns advertiser-scoped lookup. IDs in filters are still checked locally;
    # the caller cannot authorize a foreign object by declaring a different account.
    filtering = request.query.get('filtering')
    if isinstance(filtering, str):
        try:
            filtering = json.loads(filtering)
        except ValueError:
            raise GatewayError('INVALID_REQUEST', 400, 'Invalid filter JSON.') from None
    if filtering is not None:
        if not isinstance(filtering, dict):
            raise GatewayError('INVALID_REQUEST', 400, 'Invalid filtering object.')
        for key, typ in (('campaign_ids', 'campaign'), ('adgroup_ids', 'adgroup'), ('ad_ids', 'ad'), ('smart_plus_ad_ids', 'ad')):
            if key in filtering:
                ids = filtering[key]
                if not isinstance(ids, list) or len(ids) > 100:
                    raise GatewayError('INVALID_REQUEST', 400, 'Invalid object filter.')
                for object_id in ids:
                    state.require_object(request.platform, str(object_id), request.account_id, typ)
