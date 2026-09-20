"""Fixture reuse without inheriting or re-running the baseline test methods."""
from __future__ import annotations
import asyncio
import contextlib
import inspect
import io
import os
import unittest
from unittest.mock import patch
import httpx
import requests
import test_gateway as baseline
from motata_cli.transport.gateway import RemoteGatewayTransport


class CompletionCase(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = baseline.GatewayTests.asyncSetUp
    body = baseline.GatewayTests.body
    token = baseline.GatewayTests.token
    post = baseline.GatewayTests.post
    assert_no_upstream = baseline.GatewayTests.assert_no_upstream

    def claims(self):
        return {**baseline.GatewayTests.claims(self),
                'scope': 'gateway:use meta:read meta:write tiktok:read tiktok:write'}

    async def asyncTearDown(self):
        await self.service.close()
        if self.service.downloads.http is not None:
            await self.service.downloads.http.aclose()
        await baseline.GatewayTests.asyncTearDown(self)

    def grant(self, platform, account, reference=None):
        self.state.grant(subject='agent-a',client='motata-cli',workspace='single-user',
            platform=platform,account=account,reference=reference or platform+'-main')

    async def router(self, callback):
        await self.http.aclose()
        async def handler(request):
            self.seen.append(request)
            value=callback(request)
            if inspect.isawaitable(value): value=await value
            return value if isinstance(value,httpx.Response) else httpx.Response(200,json=value)
        self.http=httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False)
        self.service.http=self.http

    def media_router(self, callback):
        self.media_seen=[]
        async def handler(request):
            self.media_seen.append(request)
            value=callback(request)
            if inspect.isawaitable(value): value=await value
            return value if isinstance(value,httpx.Response) else httpx.Response(200,content=value)
        self.service.downloads.http=httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False)
        self.service.downloads.owns_http=True

    async def invoke(self, callback, extra_env=None):
        loop=asyncio.get_running_loop(); remote=self.client
        class Session:
            trust_env=False
            def post(_,url,**kw):
                if 'data' in kw:
                    value=kw['data']
                    body=value if isinstance(value,bytes) else b''.join(value)
                    call=remote.post(url,content=body,headers=kw['headers'])
                else:
                    call=remote.post(url,json=kw.get('json',{}),headers=kw['headers'])
                result=asyncio.run_coroutine_threadsafe(call,loop).result(30)
                response=requests.Response();response.status_code=result.status_code
                response.headers.update(result.headers);response._content=result.content
                response._content_consumed=True
                return response
            def close(_): pass
        self.public_seen = getattr(self,'public_seen',[])
        def public_get(url, **kw):
            # Independent mock for non-platform product intake. Nothing connects
            # to the network, and any direct advertising API request still fails.
            if not url.startswith('https://shop.example.com/'):
                raise AssertionError('direct platform network forbidden')
            self.public_seen.append((url,kw.get('headers',{})))
            self.assertNotIn(self.meta_secret,str(kw));self.assertNotIn(self.tiktok_secret,str(kw))
            result=requests.Response();result.status_code=200;result.url=url
            result.headers['Content-Type']='text/html; charset=utf-8'
            result._content=b'<html><head><title>Test product</title><meta name="description" content="Test product shop"></head><body><h1>Test Product</h1><p>Product information and price $10.00</p></body></html>'
            result._content_consumed=True
            return result
        env={'MOTATA_AUTH_MODE':'gateway','MOTATA_GATEWAY_URL':'https://gateway.example.com',
             'MOTATA_GATEWAY_JWT':self.token(),'MOTATA_GATEWAY_META_ACCOUNT_ID':'123',
             'MOTATA_GATEWAY_TIKTOK_ACCOUNT_ID':'123','MOTATA_SUPPRESS_SKILLS_NOTICE':'1',
             'MOTATA_HOME':str(self.root/'home')}
        env.update(extra_env or {})
        def work():
            client=RemoteGatewayTransport('https://gateway.example.com',session=Session())
            with patch.dict(os.environ,env,clear=True), \
                 patch('motata_cli.transport.gateway.RemoteGatewayTransport.from_environment',return_value=client), \
                 patch('requests.request',side_effect=AssertionError('direct platform network forbidden')), \
                 patch('requests.get',side_effect=public_get), \
                 patch('requests.post',side_effect=AssertionError('direct platform network forbidden')):
                return callback(client)
        return await asyncio.to_thread(work)

    async def run_cli(self, argv, extra_env=None):
        def run(_):
            from motata_cli.__main__ import main
            out,err=io.StringIO(),io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                 patch('motata_cli.__main__.ensure_dirs'),patch('motata_cli.__main__.maybe_emit_skills_drift_notice'):
                code=main(argv)
            return code,out.getvalue(),err.getvalue()
        result=await self.invoke(run,extra_env)
        self.assertNotIn(self.meta_secret,str(result));self.assertNotIn(self.tiktok_secret,str(result))
        return result
