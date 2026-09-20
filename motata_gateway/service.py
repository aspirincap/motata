"""Trusted request pipeline: JWT -> scope/grants/ownership -> credentials -> upstream."""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import time
from typing import Any
import httpx
from .errors import GatewayError
from .jwt_auth import JWTVerifier, Principal
from .limits import Limits, Scheduler, RateBudget
from .policy import select, validate_scope, check_objects, request_accounts, Endpoint
from .protocol import PlatformRequest
from .responses import PaginationStore, sanitize, strict_json, credential_values, write_evidence
from .state import GatewayState
from .credentials import CredentialResolver


class GatewayService:
    def __init__(self, *, verifier: JWTVerifier, state: GatewayState,
                 meta_version: str, limits: Limits | None = None,
                 http: httpx.AsyncClient | None = None,
                 credentials: CredentialResolver | None = None, rates: RateBudget | None = None):
        if not re.fullmatch(r'v[0-9]{1,2}\.0', meta_version):
            raise ValueError('Explicit reviewed Meta API version required')
        self.verifier, self.state, self.meta_version = verifier, state, meta_version
        self.credentials = credentials or CredentialResolver(state)
        self.limits = limits or Limits()
        self.scheduler = Scheduler(self.limits)
        self.rates = rates or RateBudget()
        self.pages = PaginationStore()
        self.account_pages = {}
        from .page_credentials import PageCredentialStore
        from .downloads import DownloadStore
        self.page_credentials = PageCredentialStore()
        self.downloads = DownloadStore(state.directory / 'downloads')
        from .uploads import UploadManager
        self.uploads = UploadManager(state.directory / 'uploads')
        self.http = http or httpx.AsyncClient(
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(120, connect=10, pool=10),
            limits=httpx.Limits(max_connections=self.limits.upstream, max_keepalive_connections=self.limits.upstream))
        self.owns_http = http is None

    async def close(self):
        self.page_credentials.close()
        await self.downloads.close()
        await self.credentials.close()
        if hasattr(self.verifier, 'close'):
            await self.verifier.close()
        if self.owns_http:
            await self.http.aclose()

    def authenticate(self, authorization: str) -> Principal:
        if not authorization.startswith('Bearer ') or authorization.count(' ') != 1:
            raise GatewayError('UNAUTHENTICATED', 401, 'Gateway bearer authentication is required.')
        principal = self.verifier.verify(authorization[7:])
        self.state.check_principal(principal)
        return principal

    async def authenticate_async(self, authorization: str) -> Principal:
        if not hasattr(self.verifier, 'verify_async'):
            return self.authenticate(authorization)
        if not authorization.startswith('Bearer ') or authorization.count(' ') != 1:
            raise GatewayError('UNAUTHENTICATED', 401, 'Gateway bearer authentication is required.')
        principal = await self.verifier.verify_async(authorization[7:])
        self.state.check_principal(principal)
        return principal

    async def resolve_object(self, request, principal):
        if (request.platform != 'meta' or request.method != 'GET' or
            not re.fullmatch(r'[0-9]{1,32}', request.path) or request.query not in ({},{'object_type':'video'}) or request.body or request.uploads):
            raise GatewayError('INVALID_REQUEST', 400, 'Object resolution only accepts an account and a numeric object ID.')
        validate_scope(principal, Endpoint('meta.resolve', 'meta:read'))
        reference = self.state.authorize(principal, 'meta', request.account_id, request.credential_ref)
        if request.query.get('object_type') == 'video':
            probe=request.model_copy(update={'path':'act_'+request.account_id+'/advideos',
                                            'query':{'video_id':request.path}})
            await self.prepare_objects(probe,principal)
            self.state.require_object('meta',request.path,request.account_id,'video')
            return {'protocol':'motata-gateway/v1','ok':True,'status':200,
                    'data':{'id':request.path,'account_id':request.account_id,'verified':True}}
        try:
            self.state.require_object('meta', request.path, request.account_id)
        except GatewayError:
            async with self.scheduler.execution(('meta', request.account_id)):
                if principal.expires_at <= time.time():
                    raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired.')
                self.state.authorize(principal, 'meta', request.account_id, reference)
                await self.rates.wait(('meta', request.account_id))
                self.state.authorize(principal, 'meta', request.account_id, reference)
                if principal.expires_at <= time.time():
                    raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired while rate-limited.')
                lease = await self.credentials.resolve(reference, 'meta', principal.workspace_id, request.account_id)
                self.state.authorize(principal, 'meta', request.account_id, reference)
                if principal.expires_at <= time.time():
                    raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired.')
                # A bounded metadata probe only, NOT the caller's requested business fields.
                probe = request.model_copy(update={'query': {'fields': 'id,account_id'}})
                status, payload = await self.upstream(probe, lease.value)
                if (status != 200 or payload.get('error') or str(payload.get('id')) != request.path
                        or str(payload.get('account_id', '')).removeprefix('act_') != request.account_id):
                    raise GatewayError('OBJECT_NOT_AUTHORIZED', 403, 'Object ownership could not be established.')
                self.state.authorize(principal, 'meta', request.account_id, reference)
                if principal.expires_at <= time.time():
                    raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired during object verification.')
                self.state.bind_object('meta', request.path, request.account_id, 'adobject')
        return {'protocol': 'motata-gateway/v1', 'request_id': request.request_id, 'ok': True, 'status': 200,
                'data': {'id': request.path, 'account_id': request.account_id, 'verified': True}}

    def authorize_request(self, request, principal, reference=None):
        primary = self.state.authorize(principal, request.platform, request.account_id,
                                       reference if reference is not None else request.credential_ref)
        for account in request_accounts(request):
            self.state.authorize(principal, request.platform, account, None)
        return primary

    async def list_accounts(self, parsed, principal):
        from .policy import _fields
        import secrets as secure_random
        if not isinstance(parsed,dict) or set(parsed)-{'platform','query','cursor'} or parsed.get('platform') not in ('meta','tiktok'):
            raise GatewayError('INVALID_REQUEST',400,'Invalid account discovery request.')
        platform=parsed['platform']
        validate_scope(principal,Endpoint('accounts.list',platform+':read'))
        self.state.check_principal(principal)
        owner=(principal.workspace_id,principal.subject,principal.client_id,principal.session_id)
        now=time.time()
        self.account_pages={k:v for k,v in self.account_pages.items() if v[0]>now}
        query=parsed.get('query') or {}
        if parsed.get('cursor'):
            saved=self.account_pages.get(parsed['cursor'])
            if not saved or saved[1]!=owner or saved[2]!=platform or query:
                raise GatewayError('PAGINATION_EXPIRED',400,'Account cursor is unavailable; restart discovery.')
            query=saved[3]
        if not isinstance(query,dict) or set(query)-{'fields','limit','after'}:
            raise GatewayError('INVALID_REQUEST',400,'Unknown account discovery option.')
        fields=query.get('fields')
        if fields is not None: _fields(fields)
        limit=query.get('limit',100)
        if isinstance(limit,bool): raise GatewayError('INVALID_REQUEST',400,'Invalid account page size.')
        try: limit=int(limit)
        except (ValueError,TypeError): raise GatewayError('INVALID_REQUEST',400,'Invalid account page size.') from None
        if not 1<=limit<=1000: raise GatewayError('INVALID_REQUEST',400,'Account page size outside bounds.')
        after=str(query.get('after',''))
        if after and not after.isdigit(): raise GatewayError('INVALID_REQUEST',400,'Invalid account cursor.')
        accounts=sorted(self.state.accounts_for(principal,platform),key=int)
        remaining=[a for a in accounts if not after or int(a)>int(after)]
        selected=remaining[:limit];rows=[];total_bytes=0
        for account in selected:
            if fields is None:
                rows.append({'id':'act_'+account,'account_id':account} if platform=='meta' else {'advertiser_id':account})
                continue
            req=PlatformRequest(protocol='motata-gateway/v1',request_id=__import__('uuid').uuid4().hex,
                platform=platform,account_id=account,method='GET',
                path='act_'+account if platform=='meta' else 'advertiser/info/',
                query={'fields':fields} if platform=='meta' else {'advertiser_ids':[account],'fields':fields})
            response=await self.dispatch(req,principal)
            value=response['data']
            total_bytes+=len(json.dumps(value,ensure_ascii=False).encode())
            if total_bytes>self.limits.response_bytes:
                raise GatewayError('ACCOUNT_DISCOVERY_INCOMPLETE',502,'Account page exceeded its aggregate byte limit; reduce limit.')
            if response['status']!=200 or value.get('error') or (platform=='tiktok' and value.get('code') not in (0,'0',None)):
                raise GatewayError('ACCOUNT_DISCOVERY_INCOMPLETE',502,'Authorized account metadata could not be fetched.')
            if platform=='meta':
                if str(value.get('id'))!='act_'+account or str(value.get('account_id',account)).removeprefix('act_')!=account:
                    raise GatewayError('RESPONSE_BLOCKED',502,'Account metadata identity mismatch.')
                rows.append(value)
            else:
                from .resource_evidence import collection_rows
                records=collection_rows(value)
                if len(records)!=1 or str(records[0].get('advertiser_id'))!=account:
                    raise GatewayError('RESPONSE_BLOCKED',502,'Advertiser metadata identity mismatch.')
                rows.extend(records)
        data={'data':rows,'source':'gateway_grants'}
        if len(remaining)>limit:
            if len(self.account_pages)>=4096: raise GatewayError('GATEWAY_BUSY',503,'Account pagination capacity reached.')
            key=secure_random.token_urlsafe(24)
            self.account_pages[key]=(now+300,owner,platform,{**query,'after':selected[-1]})
            data['paging']={'next':'motata-accounts:'+key,'cursors':{'after':selected[-1]}}
        return {'protocol':'motata-gateway/v1','ok':True,'status':200,'data':data}

    async def list_page_credentials(self, request, principal):
        if request.platform != 'meta' or request.method != 'GET' or request.path != 'me/accounts' or request.body or request.uploads or request.page_credential_ref or set(request.query) - {'fields', 'limit'}:
            raise GatewayError('INVALID_REQUEST',400,'Invalid Page discovery request.')
        validate_scope(principal, Endpoint('meta.page.credentials','meta:read'))
        from .policy import _fields
        if request.query.get('fields'): _fields(request.query['fields'])
        ref=self.authorize_request(request,principal)
        async with self.scheduler.execution(('meta',request.account_id)):
            lease=await self.credentials.resolve(ref,'meta',principal.workspace_id,request.account_id)
            descriptor=self.state.describe(ref,'meta')
            async def rows(path, fields):
                values=[];seen=set();total_bytes=0;query={'fields':fields,'limit':100}
                for _ in range(100):
                    self.authorize_request(request,principal,ref)
                    if principal.expires_at<=time.time():
                        raise GatewayError('UNAUTHENTICATED',401,'Gateway session expired during Page discovery.')
                    await self.rates.wait(('meta',request.account_id))
                    probe=request.model_copy(update={'path':path,'query':query})
                    status,payload=await self.upstream(probe,lease.value)
                    if status!=200 or payload.get('error') or not isinstance(payload.get('data'),list):
                        raise GatewayError('PAGE_AUTH_REQUIRED',403,'Page discovery failed; check platform permissions.')
                    total_bytes+=len(json.dumps(payload,ensure_ascii=False).encode())
                    if total_bytes>self.limits.response_bytes:
                        raise GatewayError('PAGE_DISCOVERY_INCOMPLETE',502,'Page discovery exceeded its aggregate byte limit.')
                    values.extend(payload['data'])
                    if len(values)>10000: break
                    nxt=(payload.get('paging') or {}).get('next')
                    if not nxt: return values
                    # Fixed path and origin, only cursor is used, not upstream token.
                    from urllib.parse import urlsplit, parse_qsl
                    parts=urlsplit(nxt)
                    if parts.scheme!='https' or parts.netloc!='graph.facebook.com' or parts.path!=f'/{self.meta_version}/{path}' or parts.fragment:
                        raise GatewayError('RESPONSE_BLOCKED',502,'Untrusted Page pagination.')
                    pairs=parse_qsl(parts.query,keep_blank_values=True)
                    cursors=[v for k,v in pairs if k=='after']
                    if len(cursors)!=1 or cursors[0] in seen or len(cursors[0])>4096:
                        raise GatewayError('RESPONSE_BLOCKED',502,'Invalid Page cursor.')
                    seen.add(cursors[0]);query={**query,'after':cursors[0]}
                raise GatewayError('PAGE_DISCOVERY_INCOMPLETE',502,'Page discovery reached its bounded page limit.')
            allowed=await rows('act_'+request.account_id+'/promote_pages','id,name')
            allowed_ids={str(x['id']) for x in allowed if isinstance(x,dict) and str(x.get('id','')).isdigit()}
            visible=await rows('me/accounts','id,name,tasks,access_token,instagram_business_account{id,username},connected_instagram_account{id,username}') if allowed_ids else []
            self.authorize_request(request,principal,ref)
            if self.state.describe(ref,'meta')!=descriptor:
                raise GatewayError('CREDENTIAL_CHANGED',503,'Page parent authorization changed.')
            new_secrets=(*lease.sensitive_values,lease.value,*credential_values(visible))
            output=[]
            for row in visible:
                if not isinstance(row,dict) or str(row.get('id')) not in allowed_ids: continue
                page=str(row['id']); self.state.bind_object('meta',page,request.account_id,'page')
                safe=sanitize(row,new_secrets)
                safe['account_id']=request.account_id
                if isinstance(row.get('access_token'),str) and row['access_token']:
                    safe['page_credential_ref']=self.page_credentials.put(principal=principal,
                        account=request.account_id,parent_ref=ref,parent_revision=descriptor.revision,
                        parent_digest=hashlib.sha256(lease.value.encode()).hexdigest(),
                        page=page,token=row['access_token'],expires_at=lease.expires_at)
                    safe['credential_available']=True
                else: safe['credential_available']=False
                output.append(safe)
            return {'protocol':'motata-gateway/v1','request_id':request.request_id,'ok':True,'status':200,'data':{'data':output}}

    async def send_download(self, key, principal, send):
        row=self.downloads.authorize(key,principal,self.state)
        async with self.scheduler.execution((row.platform,row.accounts[0])):
            await self.rates.wait((row.platform,row.accounts[0]))
            async with self.downloads.fetch(key,principal,self.state) as (path,size,sha):
                await send({'type':'http.response.start','status':200,'headers':[
                    (b'content-type',b'application/octet-stream'),(b'cache-control',b'no-store'),
                    (b'x-content-type-options',b'nosniff'),(b'content-length',str(size).encode()),
                    (b'x-motata-sha256',sha.encode())]})
                with path.open('rb') as stream:
                    while True:
                        chunk=stream.read(65536)
                        if not chunk: break
                        await send({'type':'http.response.body','body':chunk,'more_body':True})
                await send({'type':'http.response.body','body':b'','more_body':False})

    async def prepare_objects(self, request, principal):
        """Only bounded, read-only membership probes, never hidden creation probes."""
        try:
            check_objects(request,self.state)
            return
        except GatewayError as error:
            if error.code!='OBJECT_NOT_AUTHORIZED': raise
        from .policy import required_resources
        from .resource_evidence import record, collection_rows
        candidates=required_resources(request)
        if len(candidates)>100:
            raise GatewayError('INVALID_REQUEST',400,'Too many resource references in one request.')
        meta_edges={'page':'promote_pages','pixel':'adspixels','instagram':'instagram_accounts',
                    'image':'adimages','video':'advideos','app':'advertisable_applications',
                    'audience':'customaudiences'}
        tt_edges={'campaign':('campaign/get/','campaign_ids'), 'adgroup':('adgroup/get/','adgroup_ids'),
                  'ad':('ad/get/','ad_ids'), 'video':('file/video/ad/info/','video_ids'),
                  'image':('file/image/ad/info/','image_ids'), 'pixel':('pixel/list/',None),
                  'identity':('identity/get/',None),'portfolio':('creative/portfolio/get/','creative_portfolio_id'),
                  'store':('gmv_max/store/list/',None)}
        for typ,ident in candidates:
            try:
                self.state.require_object(request.platform,ident,request.account_id,typ)
                continue
            except GatewayError: pass
            # Business Center and catalog cross-account capability is an explicit
            # trusted binding; do not self-grant it by reading a broad user token.
            if typ in ('bc','catalog','upload_session'): continue
            validate_scope(principal,Endpoint('resource.probe',request.platform+':read'))
            ref=self.authorize_request(request,principal)
            if request.platform=='meta':
                if typ in ('campaign','adset','ad','creative'):
                    path=ident;query={'fields':'id,account_id'}
                elif typ in meta_edges:
                    path='act_'+request.account_id+'/'+meta_edges[typ]
                    query={'fields':'id,hash' if typ=='image' else 'id','limit':100}
                else: continue
            else:
                if typ not in tt_edges: continue
                path,key=tt_edges[typ];query={'advertiser_id':request.account_id}
                if key:
                    if key in ('campaign_ids','adgroup_ids','ad_ids'): query['filtering']={key:[ident]}
                    else: query[key]=[ident] if key.endswith('_ids') else ident
            probe=request.model_copy(update={'method':'GET','path':path,'query':query,'body':None,
                'uploads':[],'idempotency_key':None,'page_credential_ref':None})
            async with self.scheduler.execution((request.platform,request.account_id)):
                lease=await self.credentials.resolve(ref,request.platform,principal.workspace_id,request.account_id)
                for page in range(100):
                    self.authorize_request(request,principal,ref)
                    if principal.expires_at<=time.time():
                        raise GatewayError('UNAUTHENTICATED',401,'Authorization expired during object verification.')
                    await self.rates.wait((request.platform,request.account_id))
                    self.authorize_request(request,principal,ref)
                    status,data=await self.upstream(probe,lease.value)
                    if status!=200 or data.get('error') or request.platform=='tiktok' and data.get('code') not in (0,'0'):
                        break
                    if request.platform=='meta' and path==ident:
                        if str(data.get('id'))==ident and str(data.get('account_id','')).removeprefix('act_')==request.account_id:
                            self.state.bind_object('meta',ident,request.account_id,typ)
                        break
                    safe=sanitize(self.pages.rewrite(data,probe,principal,self.meta_version,(lease.value,)),(lease.value,*credential_values(data)))
                    record(safe,probe,Endpoint('probe',request.platform+':read',object_type=typ),self.state)
                    try:
                        self.state.require_object(request.platform,ident,request.account_id,typ);break
                    except GatewayError: pass
                    if request.platform=='meta':
                        nxt=(safe.get('paging') or {}).get('next')
                        if not nxt: break
                        probe=self.pages.resolve(nxt.split(':',1)[1],principal)
                    else:
                        info=(safe.get('data') or {}).get('page_info',{}) if isinstance(safe.get('data'),dict) else {}
                        total=info.get('total_page') or 1
                        if not isinstance(total,int) or page+1>=total: break
                        probe=probe.model_copy(update={'query':{**probe.query,'page':page+2}})
        check_objects(request,self.state)

    def preauthorize_upload(self, request, principal):
        endpoint = select(request)
        validate_scope(principal, endpoint)
        self.authorize_request(request, principal)
        check_objects(request, self.state)
        if not endpoint.write or not request.idempotency_key:
            raise GatewayError('IDEMPOTENCY_REQUIRED', 400, 'Upload requires a stable write key.')

    async def dispatch(self, request: PlatformRequest, principal: Principal, *, files=None) -> dict:
        if bool(request.uploads) != bool(files):
            raise GatewayError('INVALID_REQUEST', 400, 'Upload descriptors require the binary upload channel.')
        endpoint = select(request)
        validate_scope(principal, endpoint)
        reference = self.authorize_request(request, principal)
        await self.prepare_objects(request, principal)
        if request.page_credential_ref:
            self.page_credentials.resolve(request.page_credential_ref,request,principal,self.state)
        if endpoint.write and not request.idempotency_key:
            raise GatewayError('IDEMPOTENCY_REQUIRED', 400, 'A stable idempotency key is required for writes.')
        key = (request.platform, request.account_id)
        async with self.scheduler.execution(key):
            # Re-check revocation and grants after any queue wait, before resolving a token.
            if principal.expires_at <= time.time():
                raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired while queued.')
            reference = self.authorize_request(request, principal, reference)
            check_objects(request, self.state)
            owner = self.state.owner(principal, request.platform, request.account_id)
            fingerprint = hashlib.sha256(json.dumps(
                {k: v for k, v in request.model_dump().items() if k not in ('request_id', 'idempotency_key')},
                sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            if endpoint.write:
                previous = self.state.lookup_write(owner, request.idempotency_key, fingerprint)
                if previous is not None:
                    return {**previous, 'request_id': request.request_id, 'replayed': True}
            await self.rates.wait((request.platform, request.account_id))
            # Rate waiting is BEFORE credentials and BEFORE a pending mutation receipt.
            self.authorize_request(request, principal, reference)
            if principal.expires_at <= time.time():
                raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired while rate-limited.')
            lease = await self.credentials.resolve(reference, request.platform, principal.workspace_id, request.account_id)
            # External fetch may have waited: recheck identity, grants and ownership
            # immediately before a write receipt or any requested platform operation.
            if principal.expires_at <= time.time():
                raise GatewayError('UNAUTHENTICATED', 401, 'Gateway access token expired while resolving credentials.')
            self.authorize_request(request, principal, reference)
            check_objects(request, self.state)
            if endpoint.write:
                previous = self.state.begin_write(owner, request.idempotency_key, fingerprint)
                if previous is not None:
                    return {**previous, 'request_id': request.request_id, 'replayed': True}
            page_lease = (self.page_credentials.resolve(request.page_credential_ref,request,principal,self.state)
                          if request.page_credential_ref else None)
            if page_lease and page_lease.parent_digest != hashlib.sha256(lease.value.encode()).hexdigest():
                raise GatewayError('PAGE_AUTH_REQUIRED',403,'Parent credential was rotated; renew the Page reference.')
            active_token = page_lease.value if page_lease else lease.value
            secrets = (lease.value, active_token, *lease.sensitive_values)
            try:
                status, data = (await self.upstream(request, active_token, files=files) if files
                                else await self.upstream(request, active_token))
                error = data.get('error')
                if status == 401 or (request.platform == 'meta' and isinstance(error, dict) and error.get('code') == 190):
                    self.credentials.invalidate(lease)  # NEXT request refetches; never replay this write.
                secrets = (*secrets, *credential_values(data))
                # No upstream status is interpreted as safe-to-retry mutation by default.
                data = self.pages.rewrite(data, request, principal, self.meta_version, secrets)
                from .resource_evidence import filter_bc, filter_bc_assets, record
                if request.platform == 'tiktok' and request.path == 'bc/get/':
                    data = filter_bc(data,request,self.state)
                if request.platform == 'tiktok' and request.path == 'bc/asset/get/':
                    data = filter_bc_assets(data,request,principal,self.state)
                data = self.downloads.rewrite(data,request,principal,self.state,secrets)
                safe = sanitize(data, secrets)
                result = {'protocol': 'motata-gateway/v1', 'request_id': request.request_id,
                          'ok': True, 'status': status, 'data': safe,
                          'write_outcome': 'not_applicable' if not endpoint.write else 'unknown'}
                upstream_error = status >= 400 or 'error' in safe or (
                    request.platform == 'tiktok' and safe.get('code') not in (None, 0, '0'))
                if endpoint.write:
                    if upstream_error and 400 <= status < 500 and (safe.get('error') or {}).get('code') in (100, 190):
                        result['write_outcome'] = 'confirmed_failed'
                    elif upstream_error:
                        raise GatewayError('WRITE_NEEDS_REVIEW', 502, 'Platform failure did not establish a safe write outcome.', 'unknown')
                    else:
                        confirmed, bindings = write_evidence(request, endpoint, safe)
                        if not confirmed:
                            raise GatewayError('WRITE_NEEDS_REVIEW', 502, 'Write returned no authoritative success evidence.', 'unknown')
                        for object_type, object_id in bindings:
                            self.state.bind_object(request.platform, object_id, request.account_id, object_type)
                        result['write_outcome'] = 'confirmed_succeeded'
                    if result['write_outcome']=='confirmed_succeeded':
                        record(safe,request,endpoint,self.state)
                    self.state.finish_write(owner, request.idempotency_key, result)
                elif not upstream_error:
                    record(safe,request,endpoint,self.state)
                return result
            except GatewayError as error:
                if endpoint.write:
                    # A pending durable record remains quarantined until human reconciliation.
                    raise GatewayError(error.code, error.status, error.message, 'unknown') from None
                raise

    async def upstream(self, request: PlatformRequest, token: str, *, files=None) -> tuple[int, dict]:
        if request.platform == 'meta':
            host = 'graph-video.facebook.com' if request.path.endswith('/advideos') and request.method == 'POST' else 'graph.facebook.com'
            url = f'https://{host}/{self.meta_version}/{request.path}'
            headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        else:
            url = f'https://business-api.tiktok.com/open_api/v1.3/{request.path}'
            headers = {'Access-Token': token, 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        query = {}
        for key, value in request.query.items():
            query[key] = json.dumps(value, separators=(',', ':')) if isinstance(value, (dict, list)) else value
        kwargs: dict[str, Any] = {'params': query, 'headers': headers}
        if files:
            from .uploads import MultipartStream
            content = MultipartStream(request.body or {}, files)
            headers['Content-Type'] = 'multipart/form-data; boundary=' + content.boundary
            kwargs['content'] = content
        elif request.body is not None:
            # Meta form params that contain objects retain the existing JSON-string convention.
            if request.body_encoding == 'form':
                kwargs['data'] = {k: json.dumps(v, separators=(',', ':')) if isinstance(v, (dict, list)) else v
                                  for k, v in request.body.items()}
            else:
                kwargs['json'] = request.body
        try:
            async with asyncio.timeout(180):
                async with self.http.stream(request.method, url, **kwargs) as response:
                    if 300 <= response.status_code < 400:
                        raise GatewayError('UPSTREAM_REDIRECT_BLOCKED', 502, 'Upstream redirects require a reviewed adapter.')
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise GatewayError('RESPONSE_ENCODING_BLOCKED', 502, 'Compressed responses require a bounded decoder.')
                    # No credentials are sent to an origin chosen by the caller or a redirect.
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > self.limits.response_bytes:
                            raise GatewayError('RESPONSE_TOO_LARGE', 502, 'Platform response exceeded the byte limit.')
                    payload = strict_json(data)
                    if not isinstance(payload, dict):
                        raise ValueError('Expected JSON object')
                    return response.status_code, payload
        except GatewayError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, RecursionError, UnicodeError):
            raise GatewayError('UPSTREAM_UNAVAILABLE', 502, 'Platform request failed; inspect operation state before retrying.',
                               'not_applicable' if request.method == 'GET' else 'unknown') from None
