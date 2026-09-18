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

META_COLLECTIONS = {'campaigns': 'campaign', 'adsets': 'adset', 'ads': 'ad',
                    'adcreatives': 'creative', 'adimages': 'image', 'insights': None}
TIKTOK_COLLECTIONS = {'campaign/get/': 'campaign', 'adgroup/get/': 'adgroup',
                      'ad/get/': 'ad', 'report/integrated/get/': None}
CONTROL = {'method', 'http_method', 'method_override', 'batch', 'relative_url', 'headers', 'base_url', 'ids', 'node_ids', 'include_headers'}
CREATE_FIELDS = {'name', 'objective', 'status', 'daily_budget', 'lifetime_budget', 'bid_strategy',
                 'special_ad_categories', 'buying_type', 'spend_cap'}


def select(request: PlatformRequest) -> Endpoint:
    fields = request.query.get('fields')
    if request.platform == 'meta' and fields is not None:
        text = ','.join(fields) if isinstance(fields, list) and all(isinstance(x, str) for x in fields) else str(fields)
        # Unreviewed Graph expansions/aliases can escape an account grant or poison
        # ownership evidence. The first increment only supports flat projections.
        if any(c in text for c in '{}():%') or '.as' in text.lower():
            raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Graph field expansion requires an ownership-reviewed adapter.')
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
        # Initial reviewed write: account-rooted campaign creation. No generic Graph writes.
        if match and request.method == 'POST' and match[2] == 'campaigns':
            if request.query or not request.body or set(request.body) - CREATE_FIELDS:
                raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'This campaign payload is not covered by the gateway policy.')
            return Endpoint('meta.campaigns.create', 'meta:write', True, 'campaign')
        if request.method == 'GET' and re.fullmatch(r'[0-9]+', request.path):
            return Endpoint('meta.object.get', 'meta:read')
    if request.platform == 'tiktok' and request.method == 'GET' and request.path in TIKTOK_COLLECTIONS:
        # Multi-advertiser/biz-center report selectors are not covered by a single-account grant.
        if any(request.query.get(k) not in (None, False, '', [], {}) for k in
               ('advertiser_ids', 'bc_id', 'multi_adv_report_in_utc_time')):
            raise GatewayError('FORBIDDEN', 403, 'Multi-account selectors require a separately reviewed adapter.')
        # Query/body cannot override the selected advertiser, including serialized values.
        if str(request.query.get('advertiser_id', '')) != request.account_id:
            raise GatewayError('FORBIDDEN', 403, 'Advertiser does not match authorization context.')
        return Endpoint('tiktok.' + request.path.replace('/', '.').strip('.'), 'tiktok:read',
                        object_type=TIKTOK_COLLECTIONS[request.path])
    raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'This endpoint is not yet available in gateway mode.')


def validate_scope(principal, endpoint):
    if not {'gateway:use', endpoint.scope}.issubset(principal.scopes):
        raise GatewayError('INSUFFICIENT_SCOPE', 403, 'The access token does not permit this operation.')


def check_objects(request: PlatformRequest, state):
    if request.platform == 'meta' and re.fullmatch(r'[0-9]+', request.path):
        state.require_object('meta', request.path, request.account_id)
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
        for key, typ in (('campaign_ids', 'campaign'), ('adgroup_ids', 'adgroup'), ('ad_ids', 'ad')):
            if key in filtering:
                ids = filtering[key]
                if not isinstance(ids, list) or len(ids) > 100:
                    raise GatewayError('INVALID_REQUEST', 400, 'Invalid object filter.')
                for object_id in ids:
                    state.require_object(request.platform, str(object_id), request.account_id, typ)
