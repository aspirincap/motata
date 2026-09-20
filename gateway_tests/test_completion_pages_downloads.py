"""Page-derived credentials and media references through actual HTTP/CLI paths."""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
from unittest.mock import patch,AsyncMock
import httpcore
import httpx
from completion_helpers import CompletionCase
from motata_gateway.downloads import PublicNetworkBackend,PublicMediaTransport,validate_url
from motata_gateway.errors import GatewayError
from motata_gateway.responses import sanitize
from motata_cli.transport.gateway import GatewayAuthRef,GatewayPageRef
from motata_cli.meta.client import MetaClient


class PageCredentialTests(CompletionCase):
    async def page_setup(self, more=False):
        self.page_secret='SECRET_CANARY_PAGE_NEW_abcdefghi'
        self.page_secret_b='SECRET_CANARY_PAGE_OTHER_xyzxyz'
        def handler(req):
            path=req.url.path.removeprefix('/v23.0/')
            if path=='act_123/promote_pages':
                return {'data':[{'id':'800','name':'Allowed'}]}
            if path=='me/accounts':
                value={'data':[{'id':'800','name':'Allowed','access_token':self.page_secret},
                               {'id':'900','name':'Not allowed','access_token':self.page_secret_b}]}
                if more and not req.url.params.get('after'):
                    value['paging']={'next':'https://graph.facebook.com/v23.0/me/accounts?after=p2&access_token='+self.meta_secret}
                elif more: value={'data':[]}
                return value
            if path=='800_801':
                self.assertEqual(req.headers['authorization'],'Bearer '+self.page_secret)
                return {'id':'800_801','permalink_url':'https://www.facebook.com/posts/800_801',
                        'attachments':{'data':[{'url':'https://shop.example.com/product', 'media':{'image':{'src':'https://a.fbcdn.net/photo.jpg'}}}]}}
            raise AssertionError(path)
        await self.router(handler)

    async def page_list(self):
        return await self.client.post('/v1/meta/page-credentials',json=self.body(path='me/accounts',query={}),
            headers={'Authorization':'Bearer '+self.token()})

    async def test_page_token_only_upstream_and_actual_cli_story_fallback(self):
        await self.page_setup()
        def run(_):
            from motata_cli.meta.landing_pages import _list_page_access_tokens,_get_story_payload_with_token,DEFAULT_STORY_FIELDS
            meta=MetaClient(GatewayAuthRef('meta','123'),version='v23.0')
            refs,names,error=_list_page_access_tokens(meta)
            self.assertIsNone(error)
            self.assertEqual(set(refs),{'800'}); self.assertIsInstance(refs['800'],GatewayPageRef)
            value,error=_get_story_payload_with_token(meta,'800_801',refs['800'])
            self.assertIsNone(error)
            return value
        # The helper signature is verified against source by this actual call.
        result=await self.invoke(run)
        self.assertEqual(result['id'],'800_801')
        self.assertNotIn(self.page_secret,json.dumps(result))
        self.assertNotIn(self.page_secret_b,json.dumps(result))

    async def test_page_discovery_pages_are_bounded_and_token_urls_not_returned(self):
        await self.page_setup(more=True)
        response=await self.page_list()
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['data']['data']),1)
        self.assertEqual(len(self.seen),3)
        self.assertNotIn('access_token',response.text)
        self.assertNotIn(self.page_secret,response.text)

    async def test_page_reference_cannot_select_other_page_or_write(self):
        await self.page_setup();ref=(await self.page_list()).json()['data']['data'][0]['page_credential_ref']
        self.state.bind_object('meta','900','123','page')
        before=len(self.seen)
        for path,method in [('900_901','GET'),('800','POST')]:
            response=await self.post(self.body(path=path,method=method,query={},body={'name':'bad'} if method=='POST' else None,
                page_credential_ref=ref,idempotency_key='ref-write'))
            self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(len(self.seen),before)

    async def test_page_reference_expires_and_parent_rotation_invalidates(self):
        await self.page_setup();ref=(await self.page_list()).json()['data']['data'][0]['page_credential_ref']
        self.service.page_credentials.entries[ref].expires=time.time()-1
        response=await self.post(self.body(path='800_801',query={},page_credential_ref=ref))
        self.assertEqual(response.status_code,403)
        ref=(await self.page_list()).json()['data']['data'][0]['page_credential_ref']
        self.state.put_credential('meta-main','meta','SECRET_CANARY_REPLACEMENT')
        response=await self.post(self.body(path='800_801',query={},page_credential_ref=ref))
        self.assertEqual(response.status_code,403)

    async def test_page_reference_bound_to_session_and_account(self):
        await self.page_setup();ref=(await self.page_list()).json()['data']['data'][0]['page_credential_ref']
        self.grant('meta','456');self.state.bind_object('meta','800','456','page')
        before=len(self.seen)
        wrong=await self.post(self.body(account_id='456',path='800_801',query={},page_credential_ref=ref))
        self.assertEqual(wrong.status_code,403)
        token=self.token(claims={**self.claims(),'sid':'another-session'})
        wrong=await self.post(self.body(path='800_801',query={},page_credential_ref=ref),token=token)
        self.assertEqual(wrong.status_code,403); self.assertEqual(len(self.seen),before)

    async def test_page_pagination_host_attack_rejected(self):
        def route(req):
            if req.url.path.endswith('/promote_pages'):return {'data':[{'id':'800'}]}
            return {'data':[{'id':'800','access_token':'SECRET_CANARY_PAGE'}], 'paging':{'next':'https://evil.example/me/accounts?after=x'}}
        await self.router(route)
        response=await self.page_list()
        self.assertEqual(response.status_code,502);self.assertNotIn('SECRET_CANARY_PAGE',response.text)

    async def test_page_shared_across_two_authorized_accounts(self):
        self.grant('meta','456')
        for account in ('123','456'):self.state.bind_object('meta','800',account,'page')
        self.state.require_object('meta','800','123','page');self.state.require_object('meta','800','456','page')
        self.state.bind_object('meta','111','123','campaign')
        with self.assertRaises(GatewayError):self.state.bind_object('meta','111','456','campaign')


class MediaDownloadTests(CompletionCase):
    async def media_ref(self,url=None):
        self.state.bind_object('meta','456','123','creative')
        self.reply={'id':'456','image_url':url or 'https://a.fbcdn.net/image.jpg?sig=opaque'}
        response=await self.post(self.body(path='456',query={'fields':'id,image_url'}))
        self.assertEqual(response.status_code,200,response.text)
        reference=response.json()['data']['image_url']
        self.assertTrue(reference.startswith('motata-download:'))
        return reference

    async def fetch_ref(self,ref,token=None):
        return await self.client.post('/v1/downloads/'+ref.split(':',1)[1],json={},
            headers={'Authorization':'Bearer '+(token or self.token())})

    async def test_actual_cli_download_hash_atomic_file_and_no_platform_headers(self):
        value=b'jpeg-data-'*16000;self.media_router(lambda req:value)
        ref=await self.media_ref();dest=self.root/'download.jpg'
        result=await self.invoke(lambda t:t.download(ref,dest))
        self.assertEqual(dest.read_bytes(),value)
        self.assertEqual(result['sha256'],hashlib.sha256(value).hexdigest())
        for req in self.media_seen:
            self.assertNotIn('authorization',req.headers);self.assertNotIn('access-token',req.headers)
            self.assertNotIn('cookie',req.headers)
        self.assertEqual(list((self.root/'private'/'downloads').iterdir()),[])

    async def test_download_ref_owner_scope_and_revocation_checked(self):
        ref=await self.media_ref();self.media_router(lambda req:b'abc')
        other=self.token(claims={**self.claims(),'sid':'session-other'})
        self.assertEqual((await self.fetch_ref(ref,other)).status_code,404)
        self.state.remove_grant(subject='agent-a',client='motata-cli',workspace='single-user',platform='meta',account='123')
        self.assertEqual((await self.fetch_ref(ref)).status_code,403)
        self.assertEqual(self.media_seen,[])

    async def test_download_expiry_requires_metadata_renewal(self):
        ref=await self.media_ref();self.media_router(lambda req:b'abc')
        self.service.downloads.entries[ref.split(':')[1]].expires=time.time()-1
        self.assertEqual((await self.fetch_ref(ref)).status_code,404)
        renewed=await self.media_ref();self.assertNotEqual(ref,renewed)
        self.assertEqual((await self.fetch_ref(renewed)).content,b'abc')

    async def test_download_oversize_and_truncation_clean_spool(self):
        ref=await self.media_ref();self.service.downloads.max_bytes=10
        self.media_router(lambda req:httpx.Response(200,content=b'abc',headers={'Content-Length':'11'}))
        self.assertEqual((await self.fetch_ref(ref)).status_code,413)
        await self.service.downloads.http.aclose()
        self.media_router(lambda req:httpx.Response(200,content=b'abc',headers={'Content-Length':'8'}))
        response=await self.fetch_ref(ref)
        self.assertEqual(response.status_code,502,response.text)
        self.assertEqual(self.service.downloads.active,0)
        self.assertEqual(list((self.root/'private'/'downloads').iterdir()),[])

    async def test_download_redirect_no_credentials_forwarded(self):
        ref=await self.media_ref()
        def router(req):
            if req.url.path=='/image.jpg':return httpx.Response(302,headers={'Location':'https://b.fbcdn.net/final.jpg','Set-Cookie':'sensitive=yes'})
            self.assertNotIn('cookie',req.headers)
            return b'image'
        self.media_router(router)
        self.assertEqual((await self.fetch_ref(ref)).content,b'image')
        self.assertEqual(len(self.media_seen),2)

    async def test_download_evil_redirect_blocked_before_connection(self):
        ref=await self.media_ref();self.media_router(lambda req:httpx.Response(302,headers={'Location':'https://127.0.0.1/secret'}))
        response=await self.fetch_ref(ref)
        self.assertEqual(response.status_code,502)
        self.assertEqual(len(self.media_seen),1)

    async def test_download_secret_cross_chunk_blocks_entire_binary(self):
        ref=await self.media_ref();value=b'x'*(65536-10)+self.meta_secret.encode()+b'finish'
        self.media_router(lambda req:value)
        response=await self.fetch_ref(ref)
        self.assertEqual(response.status_code,502)
        self.assertNotIn(self.meta_secret,response.text)
        self.assertEqual(list((self.root/'private'/'downloads').iterdir()),[])

    async def test_client_integrity_failure_preserves_existing_destination(self):
        ref=await self.media_ref();dest=self.root/'old.jpg';dest.write_bytes(b'original')
        self.media_router(lambda req:b'newdata')
        original=self.service.send_download
        async def corrupted(key,p,send):
            async def alter(message):
                if message['type']=='http.response.start':
                    message={**message,'headers':[(k,b'0'*64 if k==b'x-motata-sha256' else v) for k,v in message['headers']]}
                await send(message)
            return await original(key,p,alter)
        with patch.object(self.service,'send_download',corrupted):
            with self.assertRaisesRegex(RuntimeError,'integrity'):
                await self.invoke(lambda t:t.download(ref,dest))
        self.assertEqual(dest.read_bytes(),b'original');self.assertEqual(list(self.root.glob('*.part')),[])

    async def test_reference_never_accepts_url_or_server_path(self):
        for bad in ['https://a.fbcdn.net/file','/etc/passwd','motata-download:../etc/passwd']:
            with self.assertRaises(RuntimeError):
                await self.invoke(lambda t:t.download(bad,self.root/'file'))
        self.assertEqual(len(self.seen),0)

    async def test_dns_connect_pins_validated_ip_not_hostname(self):
        resolver=AsyncMock(return_value=[(2,1,6,'',('8.8.8.8',443))])
        backend=AsyncMock(); backend.connect_tcp.return_value='stream'
        result=await PublicNetworkBackend(backend,resolver).connect_tcp('a.fbcdn.net',443)
        self.assertEqual(result,'stream');self.assertEqual(backend.connect_tcp.call_args.args[0],'8.8.8.8')
        resolver.assert_awaited_once()

    async def test_dns_private_mixed_and_metadata_addresses_rejected(self):
        for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1','100.64.1.1','192.0.2.1']:
            backend=AsyncMock();resolver=AsyncMock(return_value=[(2,1,6,'',('8.8.8.8',443)),(2,1,6,'',(ip,443))])
            with self.subTest(ip=ip),self.assertRaises(httpcore.ConnectError):
                await PublicNetworkBackend(backend,resolver).connect_tcp('a.fbcdn.net',443)
            backend.connect_tcp.assert_not_awaited()

    async def test_cdn_suffix_userinfo_control_port_not_allowed(self):
        for url in ['https://fbcdn.net.evil.example/a','https://a.fbcdn.net@evil.example/a','http://a.fbcdn.net/a',
                    'https://a.fbcdn.net:444/a','https://a.fbcdn.net./a','https://a.fbcdn.net/a\n','https://a.fbcdn.net/a#secret']:
            with self.subTest(url=url),self.assertRaises(GatewayError): validate_url(url,'meta')

    async def test_html_cache_materializes_gateway_refs_only_when_requested(self):
        ref=await self.media_ref();self.media_router(lambda req:b'image'*1000)
        def run(_):
            from motata_cli.report.html_utils import cache_remote_images,preview_cell
            self.assertNotIn('motata-download:',preview_cell(ref,ref))
            return cache_remote_images(self.root,{'id':ref})
        result=await self.invoke(run)
        self.assertEqual((self.root/result['id']).read_bytes(),b'image'*1000)

    async def test_malformed_json_like_string_still_scanned(self):
        with self.assertRaises(GatewayError):sanitize({'message':'{broken '+self.meta_secret},(self.meta_secret,))


    async def test_platform_credential_in_media_url_never_reaches_cdn(self):
        from urllib.parse import quote
        self.state.bind_object('meta', '456', '123', 'creative')
        self.media_router(lambda req: b'image')
        for value in (self.meta_secret, quote(quote(self.meta_secret, safe=''), safe='')):
            self.reply = {'id': '456', 'image_url': 'https://a.fbcdn.net/photo?auth=' + value}
            response = await self.post(self.body(path='456', query={'fields': 'image_url'}))
            self.assertEqual(response.status_code, 502)
            self.assertNotIn(self.meta_secret, response.text)
        self.assertEqual(self.media_seen, [])
        self.assertEqual(self.service.downloads.entries, {})

    async def test_cdn_redirect_cannot_steal_platform_credential(self):
        reference = await self.media_ref()
        self.media_router(lambda req: httpx.Response(302, headers={
            'Location': 'https://b.fbcdn.net/file?auth=' + self.meta_secret}))
        response = await self.fetch_ref(reference)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(self.media_seen), 1)
        self.assertNotIn(self.meta_secret, response.text)

    async def test_download_global_disk_quota_is_released(self):
        reference = await self.media_ref()
        self.service.downloads.max_total_bytes = 5
        self.media_router(lambda req: b'0123456789')
        response = await self.fetch_ref(reference)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.service.downloads.active, 0)
        self.assertEqual(self.service.downloads.reserved, 0)
        self.assertEqual(list((self.root/'private'/'downloads').iterdir()), [])
        self.service.downloads.max_total_bytes = 100
        self.assertEqual((await self.fetch_ref(reference)).content, b'0123456789')

    async def test_download_cancel_releases_spool_and_execution_slots(self):
        reference = await self.media_ref()
        started, release = asyncio.Event(), asyncio.Event()
        async def media(req):
            started.set()
            await release.wait()
            return b'image'
        self.media_router(media)
        pending = asyncio.create_task(self.fetch_ref(reference))
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(self.service.downloads.active, 0)
        self.assertEqual(self.service.downloads.reserved, 0)
        self.assertEqual(self.service.scheduler.active, 0)
        self.assertEqual(list((self.root/'private'/'downloads').iterdir()), [])

    async def test_meta_adimage_id_and_hash_are_both_scoped_evidence(self):
        self.reply = {'data': [{'id': '1234', 'hash': 'opaque-image-hash'}]}
        response = await self.post(self.body(path='act_123/adimages', query={'fields':'id,hash'}))
        self.assertEqual(response.status_code, 200, response.text)
        self.state.require_object('meta', '1234', '123', 'image')
        self.state.require_object('meta', 'opaque-image-hash', '123', 'image')
        with self.assertRaises(GatewayError):
            self.state.require_object('meta', 'opaque-image-hash', '456', 'image')


class StateUpgradeTests(CompletionCase):
    async def test_offline_schema_upgrade_preserves_credentials_grants_and_receipts(self):
        from motata_gateway.state import GatewayState
        from motata_gateway.admin import main
        from contextlib import redirect_stdout
        from io import StringIO
        with self.state.connection() as db:
            db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?)", ('owner','idempotent','hash','pending',None,1))
            db.execute('DROP TABLE asset_membership')
        with self.assertRaisesRegex(ValueError, 'offline admin'):
            GatewayState(self.state.directory)
        captured=StringIO()
        with redirect_stdout(captured):
            self.assertEqual(main(['--state-dir',str(self.state.directory),'init']),0)
        self.assertNotIn(self.meta_secret,captured.getvalue())
        restored=GatewayState(self.state.directory)
        self.assertEqual(restored.describe('meta-main','meta').provider,'direct')
        with restored.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM credentials').fetchone()[0],2)
            self.assertGreaterEqual(db.execute('SELECT count(*) FROM grants').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT state FROM receipts WHERE owner=?',('owner',)).fetchone()[0],'pending')
        restored.bind_object('tiktok','800','123','bc')
        restored.require_object('tiktok','800','123','bc')
