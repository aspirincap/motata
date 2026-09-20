"""Account-context evidence from reviewed platform responses, including shared assets.

No client submission calls these functions. BC discovery is filtered against
administrator bindings; an arbitrary BC visible to a broad token is not a grant.
"""
from __future__ import annotations
import copy
import json
from .errors import GatewayError
from .policy import request_accounts

FIELDS = {'campaign_id': 'campaign', 'adgroup_id': 'adgroup', 'adset_id': 'adset',
          'ad_id': 'ad', 'smart_plus_ad_id': 'ad', 'creative_id': 'creative',
          'video_id': 'video', 'image_id': 'image', 'image_hash': 'image',
          'page_id': 'page', 'pixel_id': 'pixel', 'identity_id': 'identity',
          'instagram_user_id': 'instagram', 'catalog_id': 'catalog',
          'store_id': 'store', 'bc_id': 'bc', 'creative_portfolio_id': 'portfolio',
          'avatar_video_id': 'video', 'task_id': 'task', 'app_id':'app', 'application_id':'app'}


def identifier(value):
    return (isinstance(value, (str, int)) and not isinstance(value, bool)
            and 0 < len(str(value)) <= 512 and not any(ord(c) < 32 for c in str(value)))


def collection_rows(payload):
    data = payload.get('data', [])
    if isinstance(data, dict):
        for key in ('list', 'accounts', 'bc_list', 'store_list', 'stores', 'catalogs', 'images', 'videos', 'items', 'asset_list', 'pages', 'identities', 'material_list', 'video_list', 'task_list'):
            rows = data.get(key)
            if isinstance(rows, list):
                return rows
        # Certain file-info endpoints return data keyed by platform asset ID.
        return []
    return data if isinstance(data, list) else []


def filter_bc(payload, request, state):
    if payload.get('code') not in (None, 0, '0'):
        return payload
    result = copy.deepcopy(payload)
    allowed = set(state.objects_for('tiktok', request.account_id, 'bc'))
    data = result.get('data')
    def selected(rows):
        return [r for r in rows if isinstance(r, dict) and str(r.get('bc_id') or (r.get('bc_info') or {}).get('bc_id') or r.get('id')) in allowed]
    if isinstance(data, list):
        result['data'] = selected(data)
    elif isinstance(data, dict):
        if not any(isinstance(data.get(name), list) for name in ('list', 'bc_list')):
            raise GatewayError('RESPONSE_BLOCKED', 502, 'Unreviewed Business Center collection shape.')
        # Do not let an alternate collection key escape the filter and then
        # become server-side BC membership evidence.
        data = {k:v for k,v in data.items() if k in ('list','bc_list','page_info')}
        result['data'] = data
        for name in ('list', 'bc_list'):
            if isinstance(data.get(name), list):
                data[name] = selected(data[name])
        # Original total reflects broad token scope, not the caller's grant.
        data.pop('total_number', None)
        if isinstance(data.get('page_info'), dict):
            data['page_info'].pop('total_number', None)
        data['scope'] = 'authorized_business_centers'
    else:
        raise GatewayError('RESPONSE_BLOCKED',502,'Invalid Business Center collection.')
    return result


def record(payload, request, endpoint, state):
    accounts = request_accounts(request)
    root_account = request.account_id if len(accounts) == 1 else None
    rows = collection_rows(payload)
    if request.path == 'bc/asset/get/' and str(request.query.get('asset_type','')).upper() in {'ADVERTISER','AD_ACCOUNT'}:
        return  # already filtered; advertiser listings are never ownership grants
    if endpoint.object_type:
        for row in rows:
            if not isinstance(row, dict):
                continue
            account = str(row.get('advertiser_id') or row.get('account_id') or root_account or '').removeprefix('act_')
            if account not in accounts:
                if account:
                    raise GatewayError('RESPONSE_BLOCKED', 502, 'Platform response escaped the authorized account set.')
                continue
            typ = endpoint.object_type
            keys = ('id', 'hash') if request.platform == 'meta' and typ == 'image' else ('id',) if request.platform == 'meta' else (typ + '_id', 'smart_plus_' + typ + '_id', 'id')
            obj = next((row[k] for k in keys if identifier(row.get(k))), None)
            if obj is not None:
                state.bind_object(request.platform, str(obj), account, typ)
            # Meta image GET rows can carry both an object ID and an image hash.
            # Creative writes use the hash; never mistake object-ID evidence for it.
            if request.platform == 'meta' and typ == 'image' and identifier(row.get('hash')):
                state.bind_object('meta', str(row['hash']), account, 'image')
    def visit(value, account=root_account, depth=0, parent=None):
        if depth > 24:
            return
        if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
            try:
                value = json.loads(value)
            except ValueError:
                return
        if isinstance(value, list):
            for item in value:
                visit(item, account, depth+1, parent)
        elif isinstance(value, dict):
            explicit = value.get('advertiser_id') or value.get('account_id')
            if explicit is not None:
                explicit = str(explicit).removeprefix('act_')
                # Ownership metadata (e.g. owner_ad_account) may legitimately refer
                # to another owner; it is NOT evidence granting that account.
                if parent in ('owner_ad_account', 'owner_business', 'exclusive_authorized_advertiser_info'):
                    return
                if explicit not in accounts:
                    raise GatewayError('RESPONSE_BLOCKED', 502, 'Unexpected account in platform data.')
                account = explicit
            if account:
                if parent == 'creative' and identifier(value.get('id')):
                    state.bind_object(request.platform, str(value['id']), account, 'creative')
                for key, typ in FIELDS.items():
                    if identifier(value.get(key)):
                        state.bind_object(request.platform, str(value[key]), account, typ)
                for key in ('object_story_id', 'effective_object_story_id'):
                    if isinstance(value.get(key), str) and '_' in value[key]:
                        page, post = value[key].split('_', 1)
                        if page.isdigit() and post.isdigit():
                            state.bind_object('meta', page, account, 'page')
                            state.bind_object('meta', value[key], account, 'post')
            for key, item in value.items():
                visit(item, account, depth+1, key)
    visit(payload)
    if request.platform == 'meta' and endpoint.object_type == 'image':
        images = payload.get('images') or {}
        for value in (images.values() if isinstance(images, dict) else []):
            if isinstance(value, dict) and identifier(value.get('hash')):
                state.bind_object('meta', value['hash'], request.account_id, 'image')
    # bc/asset/get is an explicitly granted BC scope, not arbitrary ad-account access.
    if request.platform == 'tiktok' and request.path == 'bc/asset/get/':
        typ = {'PIXEL':'pixel','CATALOG':'catalog','TIKTOK_ACCOUNT':'identity','SHOP':'store','IMAGE':'image','VIDEO':'video'}.get(str(request.query.get('asset_type','')).upper())
        if typ:
            for row in rows:
                if isinstance(row, dict):
                    ident = row.get('asset_id') or row.get(typ + '_id')
                    if identifier(ident):
                        state.bind_object('tiktok', str(ident), request.account_id, typ)


def filter_bc_assets(payload, request, principal, state):
    """BC advertiser lists may contain accounts outside the local grant. Filter
    before evidence collection and output, never infer grants from BC ownership.
    Other asset kinds require the explicit BC binding checked before dispatch.
    """
    if str(request.query.get('asset_type','')).upper() not in {'ADVERTISER','AD_ACCOUNT'}:
        return payload
    if payload.get('code') not in (None, 0, '0'):
        return payload
    result=copy.deepcopy(payload)
    allowed=set(state.accounts_for(principal,'tiktok'))
    data=result.get('data')
    def keep(row):
        if not isinstance(row,dict): return False
        ident=row.get('advertiser_id') or row.get('asset_id') or (row.get('advertiser_info') or {}).get('advertiser_id')
        return str(ident) in allowed
    if isinstance(data,list): result['data']=[r for r in data if keep(r)]
    elif isinstance(data,dict):
        if not any(isinstance(data.get(name),list) for name in ('list','asset_list')):
            raise GatewayError('RESPONSE_BLOCKED',502,'Unreviewed Business Center advertiser collection.')
        data={k:v for k,v in data.items() if k in ('list','asset_list','page_info')}
        result['data']=data
        for name in ('list','asset_list'):
            if isinstance(data.get(name),list): data[name]=[r for r in data[name] if keep(r)]
        data.pop('total_number',None)
        if isinstance(data.get('page_info'),dict): data['page_info'].pop('total_number',None)
        data['scope']='authorized_advertisers'
    else:
        raise GatewayError('RESPONSE_BLOCKED',502,'Invalid Business Center advertiser collection.')
    return result
