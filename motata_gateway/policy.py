"""Reviewed request shapes, NOT a caller-programmable authenticated proxy.

All destinations and auth locations are owned by the server. 'write' is decided
here (including POST reads/jobs), never trusted from an untrusted request flag.
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
    confirmation: str = 'id'

META_COLLECTIONS = {'campaigns': 'campaign', 'adsets': 'adset', 'ads': 'ad',
                    'adcreatives': 'creative', 'adimages': 'image', 'advideos': 'video',
                    'insights': None, 'activities': None, 'adspixels': 'pixel',
                    'promote_pages': 'page', 'instagram_accounts': 'instagram',
                    'advertisable_applications': 'app', 'applications': 'app',
                    'customaudiences': 'audience', 'saved_audiences': 'audience',
                    'targetingsearch': None, 'delivery_estimate': None, 'previews': None,
                    'assigned_pages': 'page', 'promotable_objects': None, 'reachestimate': None}
# Nested Graph traversal is intentionally limited to owned ad relationships.
META_RELATIONS = {'creative', 'campaign', 'adset', 'object_story_spec', 'asset_feed_spec',
                  'promoted_object', 'targeting', 'instagram_business_account', 'connected_instagram_account',
                  'attachments', 'subattachments', 'media', 'image', 'video', 'target', 'picture',
                  'owner_ad_account', 'from', 'application', 'thumbnails'}
META_WRITES = {'campaigns': 'campaign', 'adsets': 'adset', 'ads': 'ad',
               'adcreatives': 'creative', 'adimages': 'image', 'advideos': 'video',
               'insights': 'report'}
TIKTOK_COLLECTIONS = {
    'campaign/get/': 'campaign', 'adgroup/get/': 'adgroup', 'ad/get/': 'ad',
    'smart_plus/campaign/get/': 'campaign', 'smart_plus/adgroup/get/': 'adgroup',
    'smart_plus/ad/get/': 'ad', 'report/integrated/get/': None,
    'gmv_max/campaign/get/': 'campaign', 'campaign/gmv_max/info/': 'campaign',
    'gmv_max/report/get/': None, 'gmv_max/store/list/': 'store',
    'smart_plus/material_report/breakdown/': None, 'smart_plus/material_report/overview/': None,
    'file/video/ad/search/': 'video', 'file/video/ad/info/': 'video',
    'app/info/': None, 'app/list/': None, 'app/optimization_event/': None,
    'catalog/eventsource_bind/get/': None, 'catalog/get/': None,
    'creative/portfolio/get/': 'portfolio', 'creative/portfolio/list/': 'portfolio',
    'offline/get/': 'event_set', 'pixel/list/': 'pixel', 'identity/get/': 'identity',
    'tool/targeting/list/': None, 'tool/url_validate/': None, 'tool/vbo_status/': None,
    'page/get/': None, 'changelog/task/check/': None, 'advertiser/info/': None,
}
TIKTOK_COLLECTIONS.update({
    'file/image/ad/info/': 'image', 'tt_video/list/': 'video',
    'creative/aigc/voice/get/': None, 'creative/aigc/video/task/list/': 'task',
    'creative/aigc/video/list/': 'video', 'creative/digital_avatar/get/': 'avatar',
    'creative/digital_avatar/video/task/get/': 'task',
    'creative/digital_avatar/video/list/': 'video',
    'store/product/get/': None, 'gmv_max/video/get/': 'video',
    'identity/video/info/': 'video', 'search/region/': None,
    'changelog/task/download/': None, 'bc/get/': 'bc', 'bc/asset/get/': None,
})
TIKTOK_BC_READS = frozenset({'bc/asset/get/', 'catalog/get/', 'catalog/eventsource_bind/get/'})
TIKTOK_POST_READS = {'tool/targeting/search/', 'tool/targeting/info/',
    'gmv_max/creation/custom_anchor_video_list/get/', 'creative/smart_text/generate/'}
TIKTOK_WRITES = {}
for prefix in ('', 'smart_plus/'):
    for entity in ('campaign', 'adgroup', 'ad'):
        for action in ('create', 'update', 'status/update'):
            TIKTOK_WRITES[f'{prefix}{entity}/{action}/'] = (entity, 'id' if action == 'create' else 'tiktok_code')
TIKTOK_WRITES.update({
    'file/image/ad/upload/': ('image', 'upload'), 'file/video/ad/upload/': ('video', 'upload'),
    'advertiser/update/': (None, 'tiktok_code'), 'identity/create/': ('identity', 'id'),
    'creative/portfolio/create/': ('portfolio', 'id'),
    'changelog/task/create/': ('task', 'id'),
    'creative/asset/delete/': (None, 'tiktok_code'),
    'creative/asset/share/': (None, 'tiktok_code'),
    'creative/shareable_link/create/': (None, 'tiktok_link'),
    'creative/aigc/image_animation/task/create/': ('task', 'id'),
    'creative/aigc/video/task/create/': ('task', 'id'),
    'creative/digital_avatar/video/task/create/': ('task', 'id'),
    'file/video/ad/update/': (None, 'tiktok_code'),
})
CONTROL = {'method', 'http_method', 'method_override', 'batch', 'relative_url', 'headers',
           'base_url', 'ids', 'node_ids', 'include_headers'}
META_MUTATION_FIELDS = {
    'name', 'objective', 'status', 'daily_budget', 'lifetime_budget', 'bid_strategy',
    'bid_amount', 'bid_cap', 'bid_constraints', 'special_ad_categories', 'buying_type',
    'spend_cap', 'campaign_id', 'adset_id', 'creative', 'targeting', 'promoted_object',
    'optimization_goal', 'billing_event', 'start_time', 'end_time', 'destination_type',
    'is_adset_budget_sharing_enabled', 'is_using_l3_schedule', 'budget_remaining',
    'object_story_spec', 'object_story_id', 'asset_feed_spec', 'media_sourcing_spec',
    'url_tags', 'degrees_of_freedom_spec', 'adlabels', 'tracking_specs',
    'attribution_spec', 'pacing_type', 'conversion_domain', 'instagram_user_id',
    'title', 'description', 'file_size', 'upload_phase', 'upload_session_id',
    'start_offset', 'end_offset', 'video_id', 'fields', 'async', 'time_range',
    'date_preset', 'level', 'breakdowns', 'action_breakdowns', 'time_increment',
    'action_attribution_windows', 'action_report_time', 'filtering', 'limit',
    'use_account_attribution_setting', 'use_unified_attribution_setting',
    'ad_format', 'action_type', 'summary', 'default_summary', 'product_id_limit',
    'time_ranges', 'sort', 'export_columns', 'export_format', 'export_name',
    'frequency_control_specs', 'frequency_cap', 'is_dynamic_creative',
    'is_skadnetwork_attribution', 'execution_options', 'bid_adjustments',
    'dsa_beneficiary', 'dsa_payor', 'regional_regulated_categories', 'regional_regulation_identities',
    'is_adset_budget_sharing_enabled', 'is_budget_schedule_enabled',
    'instagram_actor_id', 'actor_id', 'page_id', 'asset_feed_spec', 'link_url', 'body',
    'call_to_action_type', 'object_type', 'image_hash', 'image_url', 'video_id',
    'dynamic_creative_spec', 'creative_sourcing_spec', 'adset_schedule', 'rf_prediction_id',
}
UPLOAD_FIELDS = {'meta': {'adimages': {'bytes'}, 'advideos': {'source', 'video_file_chunk'}},
                 'tiktok': {'file/image/ad/upload/': {'image_file'},
                            'file/video/ad/upload/': {'video_file'}}}


def _decode(value):
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            from .responses import strict_json
            return strict_json(value)
        except ValueError:
            raise GatewayError('INVALID_REQUEST', 400, 'Invalid structured parameter.') from None
    return value


def _fields(value):
    """Parse comma-separated projections with a bounded relationship grammar.

    No aliases, functions, modifiers, arbitrary graph traversal or credential
    selectors. Ordinary leaf fields remain extensible; provider never substitutes
    secrets into business data. Traversal nodes are a separate explicit set.
    """
    text = ','.join(value) if isinstance(value, list) and all(isinstance(x, str) for x in value) else value
    if not isinstance(text, str) or len(text) > 8192:
        raise GatewayError('INVALID_REQUEST', 400, 'Invalid field projection.')
    if not text:
        return
    tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_]*|[,{}]', text)
    if ''.join(tokens) != re.sub(r'\s+', '', text):
        raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Graph field modifiers or aliases are not reviewed.')
    index = 0
    def level(depth):
        nonlocal index
        if depth > 4:
            raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Field traversal depth exceeded.')
        seen = set()
        while index < len(tokens):
            field = tokens[index]
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', field) or field in seen:
                raise GatewayError('INVALID_REQUEST', 400, 'Invalid field projection.')
            if field.lower() in {'access_token', 'token', 'refresh_token', 'permissions', 'accounts'}:
                raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Sensitive Graph field is not available.')
            seen.add(field); index += 1
            if index < len(tokens) and tokens[index] == '{':
                if field not in META_RELATIONS:
                    raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Graph relationship is not reviewed.')
                index += 1
                level(depth + 1)
                if index >= len(tokens) or tokens[index] != '}':
                    raise GatewayError('INVALID_REQUEST', 400, 'Unclosed field projection.')
                index += 1
            if index == len(tokens) or tokens[index] == '}':
                return
            if tokens[index] != ',':
                raise GatewayError('INVALID_REQUEST', 400, 'Invalid field separator.')
            index += 1
            if index == len(tokens) or tokens[index] == '}':
                raise GatewayError('INVALID_REQUEST', 400, 'Trailing field separator.')
    level(0)
    if index != len(tokens):
        raise GatewayError('INVALID_REQUEST', 400, 'Invalid field nesting.')


def select(request: PlatformRequest) -> Endpoint:
    if CONTROL.intersection(request.query) or CONTROL.intersection(request.body or {}):
        raise GatewayError('INVALID_REQUEST', 400, 'Transport overrides and batch tunneling are forbidden.')
    if request.platform == 'meta':
        for params in (request.query, request.body or {}):
            if params.get('fields') is not None:
                _fields(params['fields'])
        if re.fullmatch(r'[0-9]+_[0-9]+', request.path) and request.method == 'GET':
            return Endpoint('meta.page.story', 'meta:read')
        if request.path == 'search' and request.method == 'GET':
            if request.query.get('type') not in ('adinterest','adinterestsuggestion','adgeolocation','adTargetingCategory') or set(request.query)-{'type','q','limit','class','interest_list','location_types','locale','country_code'}:
                raise GatewayError('ENDPOINT_NOT_REVIEWED',403,'Only reviewed ad-targeting searches are permitted.')
            return Endpoint('meta.targeting.search','meta:read')
        root = f'act_{request.account_id}'
        if request.method == 'GET' and request.path == root:
            return Endpoint('meta.account.get', 'meta:read')
        parts = request.path.split('/')
        if parts[0].startswith('act_') and parts[0] != root:
            raise GatewayError('FORBIDDEN', 403, 'Path account does not match authorization context.')
        if len(parts) == 2 and (parts[0] == root or re.fullmatch(r'[0-9]+', parts[0])):
            edge = parts[1]
            if request.method == 'GET' and edge in META_COLLECTIONS:
                return Endpoint(f'meta.{edge}.list', 'meta:read', object_type=META_COLLECTIONS[edge])
            if request.method == 'POST' and edge in META_WRITES:
                if request.query or not request.body or set(request.body) - META_MUTATION_FIELDS:
                    raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Mutation fields require policy review.')
                if request.uploads and {x.field for x in request.uploads} - UPLOAD_FIELDS['meta'].get(edge, set()):
                    raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Upload fields do not match endpoint.')
                # Ad hierarchy mutations are account rooted; object insights are
                # safe only after check_objects has established ownership.
                if parts[0] != root and edge != 'insights':
                    raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Nested writes are not supported.')
                return Endpoint(f'meta.{edge}.create', 'meta:read' if edge == 'insights' else 'meta:write',
                                True, META_WRITES[edge], 'report' if edge == 'insights' else 'upload' if edge in ('advideos', 'adimages') else 'id')
        if len(parts) == 1 and re.fullmatch(r'[0-9]+', request.path):
            if request.method == 'GET':
                return Endpoint('meta.object.get', 'meta:read')
            if request.method == 'DELETE':
                return Endpoint('meta.object.delete', 'meta:write', True, confirmation='meta_success')
            if request.method == 'POST' and request.body and not (set(request.body) - META_MUTATION_FIELDS):
                return Endpoint('meta.object.update', 'meta:write', True, confirmation='meta_success')
    else:
        params = request.query if request.method == 'GET' else (request.body or {})
        if request.path == 'advertiser/info/' and request.method == 'GET':
            accounts = _decode(params.get('advertiser_ids'))
            if not isinstance(accounts, list) or not accounts or len(accounts) > 100 or request.account_id not in [str(x) for x in accounts]:
                raise GatewayError('FORBIDDEN', 403, 'Account metadata needs an explicit bounded advertiser list.')
            return Endpoint('tiktok.advertiser.info', 'tiktok:read')
        if request.path == 'bc/get/' and request.method == 'GET':
            return Endpoint('tiktok.bc.list', 'tiktok:read', object_type='bc')
        if request.path in TIKTOK_BC_READS and request.method == 'GET':
            if not str(params.get('bc_id', '')).isdigit():
                raise GatewayError('INVALID_REQUEST', 400, 'A Business Center binding is required.')
            return Endpoint('tiktok.' + request.path.replace('/', '.').strip('.'), 'tiktok:read',
                            object_type='catalog' if request.path == 'catalog/get/' else None)
        if request.path == 'report/integrated/get/' and params.get('advertiser_ids'):
            accounts = _decode(params['advertiser_ids'])
            if not isinstance(accounts, list) or not accounts or len(accounts) > 100 or request.account_id not in [str(x) for x in accounts]:
                raise GatewayError('FORBIDDEN', 403, 'Report advertiser selection must be bounded and explicit.')
        elif request.path == 'file/video/ad/update/':
            if params.get('advertiser_id') not in (None, request.account_id):
                raise GatewayError('FORBIDDEN', 403, 'Advertiser mismatch.')
        elif request.path == 'creative/asset/share/':
            if str(params.get('advertiser_id') or params.get('source_advertiser_id') or '') != request.account_id:
                raise GatewayError('FORBIDDEN', 403, 'Source advertiser mismatch.')
        elif str(params.get('advertiser_id', '')) != request.account_id:
            raise GatewayError('FORBIDDEN', 403, 'Advertiser does not match authorization context.')
        if request.method == 'GET' and request.path in TIKTOK_COLLECTIONS:
            return Endpoint('tiktok.' + request.path.replace('/', '.').strip('.'), 'tiktok:read',
                            object_type=TIKTOK_COLLECTIONS[request.path])
        if request.method == 'POST' and request.path in TIKTOK_POST_READS:
            return Endpoint('tiktok.' + request.path.replace('/', '.').strip('.'), 'tiktok:read')
        if request.method == 'POST' and request.path in TIKTOK_WRITES:
            typ, confirmation = TIKTOK_WRITES[request.path]
            if request.uploads and {x.field for x in request.uploads} - UPLOAD_FIELDS['tiktok'].get(request.path, set()):
                raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'Upload fields do not match endpoint.')
            return Endpoint('tiktok.' + request.path.replace('/', '.').strip('.'),
                            'tiktok:read' if request.path == 'changelog/task/create/' else 'tiktok:write',
                            True, typ, confirmation)
    raise GatewayError('ENDPOINT_NOT_REVIEWED', 403, 'This endpoint is not yet available in gateway mode.')


def validate_scope(principal, endpoint):
    if not {'gateway:use', endpoint.scope}.issubset(principal.scopes):
        raise GatewayError('INSUFFICIENT_SCOPE', 403, 'The access token does not permit this operation.')


def request_accounts(request: PlatformRequest) -> list[str]:
    params = request.query if request.method == 'GET' else (request.body or {})
    accounts = [request.account_id]
    allowed = ('advertiser_ids',) if request.platform == 'tiktok' and request.path in (
        'advertiser/info/', 'report/integrated/get/') else ('target_advertiser_ids',) if request.platform == 'tiktok' and request.path == 'creative/asset/share/' else ()
    for key in allowed:
        value = _decode(params.get(key, []))
        if not isinstance(value, list) or len(value) > 100:
            raise GatewayError('INVALID_REQUEST', 400, 'Invalid account selection.')
        accounts.extend(str(x) for x in value)
    if not all(re.fullmatch(r'[0-9]{1,32}', a) for a in accounts):
        raise GatewayError('INVALID_REQUEST', 400, 'Invalid account ID.')
    return list(dict.fromkeys(accounts))


OBJECT_TYPES = {'campaign_id': 'campaign', 'adset_id': 'adset', 'adgroup_id': 'adgroup',
             'ad_id': 'ad', 'creative_id': 'creative', 'pixel_id': 'pixel', 'video_id': 'video',
             'upload_session_id': 'upload_session', 'page_id': 'page', 'instagram_user_id': 'instagram',
             'image_hash': 'image', 'image_id': 'image', 'identity_id': 'identity',
             'creative_portfolio_id': 'portfolio', 'avatar_video_id': 'video',
             'instagram_actor_id': 'instagram', 'application_id':'app', 'app_id':'app',
             'store_id': 'store', 'catalog_id': 'catalog', 'bc_id': 'bc',
             'store_authorized_bc_id': 'bc', 'identity_authorized_bc_id': 'bc'}

def required_resources(request):
    result=[]
    def visit(value):
        value=_decode(value)
        if isinstance(value,list):
            for v in value: visit(v)
        elif isinstance(value,dict):
            for key,v in value.items():
                scalar=key[:-1] if key.endswith('_ids') else key
                if key in ('object_story_id','effective_object_story_id') and isinstance(v,str) and re.fullmatch(r'[0-9]+_[0-9]+',v):
                    result.append(('page',v.split('_',1)[0]))
                elif scalar in OBJECT_TYPES and v not in (None,'',[]):
                    vals=_decode(v) if key.endswith('_ids') else [v]
                    if not isinstance(vals,list) or len(vals)>100:
                        raise GatewayError('INVALID_REQUEST',400,'Invalid resource selection.')
                    result.extend((OBJECT_TYPES[scalar],str(x)) for x in vals)
                else: visit(v)
    visit(request.query);visit(request.body)
    return list(dict.fromkeys(result))


def check_objects(request: PlatformRequest, state):
    root = request.path.split('/')[0]
    if request.platform == 'meta' and re.fullmatch(r'[0-9]+', root):
        state.require_object('meta', root, request.account_id)
    # All advertiser-scoped ID references, including writes, must be established
    # by authorized listings/create receipts first. No IDs from the caller become
    # ownership evidence by assertion.
    if request.platform == 'meta' and '_' in root and root.replace('_', '').isdigit():
        state.require_object('meta', root.split('_')[0], request.account_id, 'page')
    if request.page_credential_ref and request.platform != 'meta':
        raise GatewayError('FORBIDDEN', 403, 'Page reference is Meta-only.')
    types = OBJECT_TYPES
    def visit(value, depth=0):
        value = _decode(value)
        if depth > 24:
            raise GatewayError('INVALID_REQUEST', 400, 'Object nesting exceeded limit.')
        if isinstance(value, list):
            for item in value:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in ('advertiser_ids', 'target_advertiser_ids'):
                    allowed = request_accounts(request)
                    vals = _decode(child)
                    if not isinstance(vals, list) or any(str(x) not in allowed for x in vals):
                        raise GatewayError('FORBIDDEN', 403, 'Account selector is not authorized for this operation.')
                    continue
                if key in ('account_id', 'advertiser_id', 'source_advertiser_id') and str(child).removeprefix('act_') != request.account_id:
                    raise GatewayError('FORBIDDEN', 403, 'Nested account reference differs from authorization.')
                scalar = key[:-1] if key.endswith('_ids') else key
                if key in ('object_story_id','effective_object_story_id') and isinstance(child,str) and re.fullmatch(r'[0-9]+_[0-9]+',child):
                    state.require_object('meta',child.split('_',1)[0],request.account_id,'page')
                elif scalar in types and child not in (None, '', []):
                    # Advertiser-scoped READ endpoints validate ID filters upstream. For
                    # BC-scoped/global requests and all writes, explicit proof is required.
                    # BC/store/catalog selectors can broaden scope and are always checked.
                    if request.platform == 'tiktok' and request.method == 'GET' and types[scalar] not in ('bc', 'store', 'catalog') and request.path not in TIKTOK_BC_READS:
                        continue
                    values = _decode(child) if key.endswith('_ids') else [child]
                    if not isinstance(values, list) or len(values) > 100:
                        raise GatewayError('INVALID_REQUEST', 400, 'Invalid object reference list.')
                    for obj in values:
                        state.require_object(request.platform, str(obj), request.account_id, types[scalar])
                else:
                    visit(child, depth + 1)
    visit(request.query)
    visit(request.body)
