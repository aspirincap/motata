"""Opaque, account/session-bound media downloads; no caller URL or server path.

CDN DNS is checked at connect time and the validated IP is passed to the socket
backend. TLS still verifies the ORIGINAL hostname (httpcore owns SNI). Checking
DNS and subsequently resolving it again would not protect against rebinding.
All bytes are privately spooled and scanned before ANY byte crosses to a caller.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urljoin, quote, quote_plus, unquote
import httpcore
import httpx
from .errors import GatewayError
from .state import check_private

DOMAINS = {
    'meta': ('fbcdn.net', 'cdninstagram.com', 'fbsbx.com'),
    'tiktok': ('tiktokcdn.com', 'tiktokcdn-us.com', 'tiktokcdn-eu.com',
               'tiktokv.com', 'byteoversea.com', 'ibytedtos.com', 'muscdn.com'),
}
MEDIA_FIELDS = frozenset({'image_url', 'thumbnail_url', 'thumbnail', 'source', 'download_url',
    'url', 'video_url', 'preview_url', 'cover_url', 'cover', 'poster_url', 'play_url',
    'origin_image', 'origin_cover', 'download_link', 'image_urls', 'video_cover_url',
    'url_128', 'url_list', 'video_url_list', 'download_urls', 'product_image_url', 'profile_image', 'avatar_url', 'uri', 'src'})


def validate_url(url: str, platform: str, domains=None):
    if not isinstance(url, str) or len(url)>16384 or any(ord(c)<33 for c in url):
        raise GatewayError('DOWNLOAD_DESTINATION_BLOCKED',502,'Invalid media destination.')
    try:
        parsed=urlsplit(url); host=(parsed.hostname or '').lower(); port=parsed.port
    except ValueError:
        raise GatewayError('DOWNLOAD_DESTINATION_BLOCKED',502,'Invalid media destination.') from None
    if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.fragment or port not in (None,443)
        or host.endswith('.') or '\\' in url or not any(host==d or host.endswith('.'+d) for d in (domains or DOMAINS).get(platform,()))):
        raise GatewayError('DOWNLOAD_DESTINATION_BLOCKED',502,'Media origin is not reviewed.')
    try: ipaddress.ip_address(host)
    except ValueError: return parsed
    raise GatewayError('DOWNLOAD_DESTINATION_BLOCKED',502,'Literal IP media origins are forbidden.')


def reject_credential_url(url: str, known_secrets: tuple[str, ...]) -> None:
    """A reviewed CDN is not an authorization sink for platform credentials.

    Limited CDN signatures remain internal references, but actual platform or
    derived Page credentials must never be forwarded to a media origin, even
    when a platform response puts one in a URL or redirects to one.
    """
    decoded = url
    for _ in range(3):
        decoded = unquote(decoded)
    for token in known_secrets:
        if not token:
            continue
        variants = (token, quote(token, safe=''), quote_plus(token),
                    base64.b64encode(token.encode()).decode(),
                    base64.urlsafe_b64encode(token.encode()).decode().rstrip('='))
        if any(value in url or value in decoded for value in variants):
            raise GatewayError('RESPONSE_BLOCKED', 502,
                               'Platform credentials cannot be sent to a media origin.')


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend=None, resolver=None):
        self.backend = backend or httpcore.AnyIOBackend()
        self.resolver = resolver

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if port != 443:
            raise httpcore.ConnectError('Media TCP port is not allowed')
        try:
            async with asyncio.timeout(min(timeout or 10, 10)):
                resolver = self.resolver or asyncio.get_running_loop().getaddrinfo
                rows = await resolver(host, port, type=socket.SOCK_STREAM)
                ips = list(dict.fromkeys(row[4][0] for row in rows))
                if not ips or len(ips)>32 or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                    raise ValueError('non-public DNS')
                for ip in ips:
                    try:
                        return await self.backend.connect_tcp(ip, port, timeout=timeout,
                            local_address=local_address, socket_options=socket_options)
                    except (httpcore.ConnectError, httpcore.ConnectTimeout):
                        continue
        except (OSError, ValueError, TimeoutError):
            pass
        raise httpcore.ConnectError('Public media connection unavailable') from None

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError('Unix destinations forbidden for media')

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class _CoreStream(httpx.AsyncByteStream):
    def __init__(self, stream): self.stream=stream
    async def __aiter__(self):
        async for part in self.stream: yield part
    async def aclose(self): await self.stream.aclose()


class PublicMediaTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.pool = httpcore.AsyncConnectionPool(network_backend=PublicNetworkBackend(),
            max_connections=4, max_keepalive_connections=4, retries=0)
    async def handle_async_request(self, request):
        response = await self.pool.handle_async_request(httpcore.Request(
            method=request.method, url=httpcore.URL(scheme=request.url.raw_scheme,
                host=request.url.raw_host, port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions))
        return httpx.Response(response.status, headers=response.headers,
            stream=_CoreStream(response.stream), extensions=response.extensions)
    async def aclose(self): await self.pool.aclose()


@dataclass
class DownloadEntry:
    owner: tuple
    platform: str
    accounts: tuple[str, ...]
    refs: tuple[str, ...]
    revisions: tuple[int, ...]
    expires: float
    url: str = field(repr=False)
    secrets: tuple[str, ...] = field(repr=False)


class DownloadStore:
    def __init__(self, root: Path, *, ttl=900, capacity=4096, max_bytes=4_000_000_000,
                 max_active=4, max_total_bytes=8_000_000_000, domains=None, http=None):
        self.root, self.ttl, self.capacity = root, ttl, capacity
        self.max_bytes, self.max_active = max_bytes, max_active
        self.max_total_bytes=max_total_bytes
        self.entries, self.active = {}, 0
        self.domains, self.http = domains or DOMAINS, http
        self.owns_http = http is None
        self.reserved = 0

    @staticmethod
    def owner(p): return (p.workspace_id,p.subject,p.client_id,p.session_id)

    def issue(self, url, request, principal, state, known_secrets):
        validate_url(url,request.platform,self.domains)
        reject_credential_url(url, tuple(known_secrets))
        from .policy import request_accounts
        accounts = tuple(request_accounts(request))
        refs = tuple(state.authorize(principal,request.platform,a,None) for a in accounts)
        revisions = tuple(state.describe(ref,request.platform).revision for ref in refs)
        now=time.time()
        self.entries = {k:v for k,v in self.entries.items() if v.expires>now}
        if len(self.entries)>=self.capacity:
            raise GatewayError('GATEWAY_BUSY',503,'Media reference capacity reached.')
        key=secrets.token_urlsafe(24)
        self.entries[key]=DownloadEntry(self.owner(principal),request.platform,accounts,refs,revisions,
            now+self.ttl,url,tuple(x for x in known_secrets if x))
        return 'motata-download:'+key

    def rewrite(self, value, request, principal, state, known_secrets, depth=0, parent=None):
        if depth>32:
            raise GatewayError('RESPONSE_BLOCKED',502,'Media response nesting exceeded limit.')
        if isinstance(value,dict):
            return {k:self.rewrite(v,request,principal,state,known_secrets,depth+1,k) for k,v in value.items()}
        if isinstance(value,list):
            return [self.rewrite(v,request,principal,state,known_secrets,depth+1,parent) for v in value]
        if isinstance(value,str):
            if value.lstrip().startswith(('{','[')):
                from .responses import strict_json
                try: parsed=strict_json(value)
                except ValueError: pass
                else: return json.dumps(self.rewrite(parsed,request,principal,state,known_secrets,depth+1,parent),ensure_ascii=False)
            if parent in MEDIA_FIELDS and value.startswith('https://'):
                # Preserve normal business links. Only recognized media origins
                # become references; unknown signed/secret URLs are blocked later.
                host=(urlsplit(value).hostname or '').lower()
                if any(host==d or host.endswith('.'+d) for d in self.domains.get(request.platform,())):
                    return self.issue(value,request,principal,state,known_secrets)
        return value

    def authorize(self,key,principal,state):
        row=self.entries.get(key)
        if not row or row.owner!=self.owner(principal) or row.expires<=time.time():
            raise GatewayError('DOWNLOAD_EXPIRED',404,'Media reference is unavailable; refetch source metadata.')
        if principal.expires_at<=time.time() or not {'gateway:use',row.platform+':read'}.issubset(principal.scopes):
            raise GatewayError('UNAUTHENTICATED',401,'Media authorization expired.')
        for account,ref,revision in zip(row.accounts,row.refs,row.revisions):
            state.authorize(principal,row.platform,account,ref)
            if state.describe(ref,row.platform).revision!=revision:
                raise GatewayError('DOWNLOAD_EXPIRED',404,'Media authorization changed; refetch source metadata.')
        return row

    @asynccontextmanager
    async def fetch(self,key,principal,state):
        row=self.authorize(key,principal,state)
        if self.active>=self.max_active:
            raise GatewayError('GATEWAY_BUSY',503,'Download capacity reached.')
        self.active+=1
        temporary=None
        reservation=0
        try:
            self.root.mkdir(mode=0o700,parents=True,exist_ok=True);check_private(self.root,directory=True)
            fd,name=tempfile.mkstemp(prefix='download-',dir=self.root)
            temporary=Path(name)
            if self.http is None:
                self.http=httpx.AsyncClient(transport=PublicMediaTransport(),trust_env=False,
                    follow_redirects=False,timeout=httpx.Timeout(60,connect=10))
            digest=hashlib.sha256();size=0
            patterns=[]
            for token in row.secrets:
                patterns.extend(v.encode() for v in (token,quote(token,safe=''),quote_plus(token),
                    base64.b64encode(token.encode()).decode(),base64.urlsafe_b64encode(token.encode()).decode().rstrip('=')))
            overlap=max([len(p) for p in patterns] or [1])-1
            tail=b''
            with os.fdopen(fd,'wb') as output:
                async with asyncio.timeout(600):
                    url=row.url
                    for redirect in range(4):
                        validate_url(url,row.platform,self.domains)
                        reject_credential_url(url, row.secrets)
                        # Explicit Request bypasses HTTPX cookie-jar/header merging.
                        req=httpx.Request('GET',url,headers={'Accept-Encoding':'identity','Accept':'*/*'},
                            extensions={'timeout':{'connect':10,'read':60,'write':60,'pool':10}})
                        response=await self.http.send(req,stream=True,follow_redirects=False)
                        try:
                            if response.status_code in (301,302,303,307,308):
                                if redirect>=3 or not response.headers.get('location'):
                                    raise GatewayError('DOWNLOAD_REDIRECT_BLOCKED',502,'Media redirect limit exceeded.')
                                url=urljoin(url,response.headers['location'])
                                validate_url(url,row.platform,self.domains)
                                reject_credential_url(url, row.secrets)
                                continue
                            if response.status_code!=200:
                                raise GatewayError('DOWNLOAD_UNAVAILABLE',502,'Media is unavailable; refetch source metadata.')
                            if response.headers.get('content-encoding','identity').lower()!='identity':
                                raise GatewayError('DOWNLOAD_ENCODING_BLOCKED',502,'Compressed transfer is not allowed.')
                            declared=response.headers.get('content-length')
                            if declared is not None and (not declared.isdigit() or int(declared)>self.max_bytes):
                                raise GatewayError('DOWNLOAD_TOO_LARGE',413,'Media exceeds configured quota.')
                            async for chunk in response.aiter_bytes(65536):
                                size+=len(chunk)
                                if size>self.max_bytes:
                                    raise GatewayError('DOWNLOAD_TOO_LARGE',413,'Media exceeds configured quota.')
                                if self.reserved+len(chunk)>self.max_total_bytes:
                                    raise GatewayError('GATEWAY_BUSY',503,'Temporary media quota exhausted.')
                                self.reserved+=len(chunk);reservation+=len(chunk)
                                probe=tail+chunk
                                if any(p in probe for p in patterns):
                                    raise GatewayError('RESPONSE_BLOCKED',502,'Downloaded content contains credential material.')
                                tail=probe[-overlap:] if overlap else b''
                                digest.update(chunk);output.write(chunk)
                            if declared is not None and size!=int(declared):
                                raise GatewayError('DOWNLOAD_TRUNCATED',502,'Incomplete upstream download.')
                            break
                        finally:
                            await response.aclose()
                    output.flush();os.fsync(output.fileno())
            # No bytes to caller until the complete spool passed integrity/secret checks.
            self.authorize(key,principal,state)
            yield temporary,size,digest.hexdigest()
        except GatewayError: raise
        except (httpx.HTTPError,httpcore.NetworkError,httpcore.ProtocolError,TimeoutError,OSError,ValueError):
            raise GatewayError('DOWNLOAD_UNAVAILABLE',502,'Media transfer failed; no partial file was committed.') from None
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)
            self.reserved-=reservation
            self.active-=1

    async def close(self):
        self.entries.clear()
        if self.http is not None and self.owns_http: await self.http.aclose()
