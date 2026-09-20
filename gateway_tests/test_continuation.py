"""Rebuilt increment: real request builders -> gateway -> mock platform."""
from __future__ import annotations
import asyncio
import io
import json
import os
import struct
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
import requests
import test_gateway as base
from motata_cli.transport.gateway import RemoteGatewayTransport, GatewayAuthRef
from motata_cli.transport.uploads import UploadBody
from motata_cli.common.errors import CliError


class ContinuationTests(base.GatewayTests):
    def claims(self):
        return {**super().claims(), 'scope': 'gateway:use meta:read meta:write tiktok:read tiktok:write'}

    async def invoke_client(self, callback):
        loop = asyncio.get_running_loop()
        remote = self.client
        token = self.token()
        class Session:
            trust_env = False
            def post(self, url, **kw):
                if 'data' in kw:
                    # In-memory test bridge only; production uses streaming requests.
                    body = b''.join(kw['data'])
                    coroutine = remote.post(url, content=body, headers=kw['headers'])
                else:
                    coroutine = remote.post(url, json=kw['json'], headers=kw['headers'])
                response = asyncio.run_coroutine_threadsafe(coroutine, loop).result(15)
                value = requests.Response(); value.status_code = response.status_code
                value._content = response.content; value._content_consumed = True
                return value
            def close(self): pass
        def execute():
            with patch.dict(os.environ, {'MOTATA_AUTH_MODE': 'gateway', 'MOTATA_GATEWAY_JWT': token}, clear=True):
                transport = RemoteGatewayTransport('https://gateway.example.com', session=Session())
                with patch('motata_cli.transport.gateway.RemoteGatewayTransport.from_environment', return_value=transport), \
                     patch('requests.request', side_effect=AssertionError('direct network forbidden')):
                    return callback(transport)
        return await asyncio.to_thread(execute)

    async def test_sdk_campaign_create_uses_builder_without_real_token(self):
        from motata_cli.tiktok.client import TikTokClient
        self.reply = {'code': 0, 'data': {'campaign_id': '800'}}
        def run(_):
            client = TikTokClient(GatewayAuthRef('tiktok', '123'))
            self.assertIsNone(client.access_token)
            self.assertFalse(hasattr(client.api_client, 'rest_client'))
            return client.create_campaign({'advertiser_id': '123', 'campaign_name': 'SDK test', 'operation_status': 'DISABLE'})
        result = await self.invoke_client(run)
        self.assertEqual(result['data']['campaign_id'], '800')
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.seen[0].headers['Access-Token'], self.tiktok_secret)
        self.assertNotIn(self.tiktok_secret, self.seen[0].content.decode())
        self.state.require_object('tiktok', '800', '123', 'campaign')

    async def test_sdk_smart_plus_update_requires_owned_object(self):
        from motata_cli.tiktok.client import TikTokClient
        self.state.bind_object('tiktok', '800', '123', 'campaign')
        self.reply = {'code': 0, 'data': {}}
        result = await self.invoke_client(lambda _: TikTokClient(GatewayAuthRef('tiktok', '123')).update_campaign(
            {'advertiser_id': '123', 'campaign_id': '800', 'campaign_name': 'changed'}, smart_plus=True))
        self.assertEqual(result['code'], 0)
        self.assertTrue(self.seen[-1].url.path.endswith('/smart_plus/campaign/update/'))

    async def test_sdk_parameter_validation_preserved(self):
        from motata_cli.tiktok.client import TikTokClient
        with self.assertRaises(CliError):
            await self.invoke_client(lambda _: TikTokClient(GatewayAuthRef('tiktok', '123'))._invoke(
                TikTokClient(GatewayAuthRef('tiktok', '123')).campaign_api.campaign_create,
                None, unexpected='bad'))
        self.assert_no_upstream()

    async def test_sdk_no_real_access_token_override(self):
        from motata_cli.transport.tiktok_sdk import GatewaySDKClient
        with self.assertRaises(CliError):
            GatewaySDKClient(None, GatewayAuthRef('tiktok', '123')).call_api(
                '/open_api/v1.3/campaign/get/', 'GET', header_params={'Access-Token': 'SECRET'})
        self.assert_no_upstream()

    async def test_tiktok_page_get_uses_gateway(self):
        from motata_cli.tiktok.client import TikTokClient
        self.reply = {'code': 0, 'data': {'list': []}}
        result = await self.invoke_client(lambda _: TikTokClient(GatewayAuthRef('tiktok', '123')).list_pages('123'))
        self.assertEqual(result['code'], 0)
        self.assertTrue(self.seen[-1].url.path.endswith('/page/get/'))

    async def test_tiktok_image_upload_streams_via_sdk(self):
        from motata_cli.tiktok.client import TikTokClient
        image = self.root / 'creative.png'; image.write_bytes(b'not-a-real-image' * 8192)
        self.reply = {'code': 0, 'data': {'image_id': '888'}}
        result = await self.invoke_client(lambda _: TikTokClient(GatewayAuthRef('tiktok', '123')).upload_image('123', str(image)))
        self.assertEqual(result['data']['image_id'], '888')
        content = self.seen[-1].content
        self.assertIn(image.read_bytes(), content)
        self.assertIn(b'name="image_file"', content)
        self.assertNotIn(str(image).encode(), content)
        self.assertEqual(self.service.uploads.active, 0)
        self.assertEqual(self.service.uploads.reserved, 0)
        self.assertEqual(list(self.service.uploads.root.iterdir()), [])

    async def test_meta_image_upload_streams_and_returns_hash(self):
        from motata_cli.meta.client import MetaClient
        self.reply = {'images': {'x.png': {'hash': 'abcd1234'}}}
        result = await self.invoke_client(lambda _: MetaClient(GatewayAuthRef('meta', '123'), version='v23.0').post(
            'act_123/adimages', data={'name': 'local'}, files={'bytes': ('x.png', io.BytesIO(b'png-data'), 'image/png')}))
        self.assertEqual(result['images']['x.png']['hash'], 'abcd1234')
        self.assertNotIn(self.meta_secret.encode(), self.seen[-1].content)

    async def framed(self, payload, files):
        with UploadBody(files) as upload:
            body = b''.join(upload.encode(payload))
        return await self.client.post('/v1/platform/upload', content=body, headers={'Authorization': 'Bearer ' + self.token()})

    def upload_payload(self):
        return self.body(path='act_123/adimages', method='POST', query={}, body={'name': 'file'}, idempotency_key='upload-test')

    async def test_upload_hash_mismatch_never_resolves_credential(self):
        with UploadBody({'bytes': ('x.png', io.BytesIO(b'abc'))}) as upload:
            content = b''.join(upload.encode(self.upload_payload()))
        response = await self.client.post('/v1/platform/upload', content=content[:-1] + b'd',
                                         headers={'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'UPLOAD_HASH_MISMATCH')
        self.assert_no_upstream()
        self.assertEqual(self.service.uploads.reserved, 0)

    async def test_upload_cross_account_rejected_before_disk_or_token(self):
        response = await self.framed({**self.upload_payload(), 'account_id': '999'}, {'bytes': ('x.png', io.BytesIO(b'abc'))})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.service.uploads.root.exists())
        self.assert_no_upstream()

    async def test_upload_descriptors_on_json_route_rejected(self):
        with UploadBody({'bytes': ('x.png', io.BytesIO(b'abc'))}) as upload:
            response = await self.post({**self.upload_payload(), 'uploads': upload.specs})
        self.assertEqual(response.status_code, 400)
        self.assert_no_upstream()

    async def test_upload_size_quota(self):
        self.service.uploads.max_file = 2
        response = await self.framed(self.upload_payload(), {'bytes': ('x.png', io.BytesIO(b'abc'))})
        self.assertEqual(response.status_code, 413)
        self.assert_no_upstream()

    async def test_upload_truncated_and_trailing_rejected(self):
        with UploadBody({'bytes': ('x.png', io.BytesIO(b'abc'))}) as upload:
            encoded = b''.join(upload.encode(self.upload_payload()))
        for body in (encoded[:-1], encoded + b'x'):
            response = await self.client.post('/v1/platform/upload', content=body, headers={'Authorization': 'Bearer ' + self.token()})
            self.assertEqual(response.status_code, 400)
            self.assert_no_upstream()
        self.assertEqual(list(self.service.uploads.root.iterdir()), [])

    async def test_upload_field_mismatch_cannot_read_file(self):
        response = await self.framed(self.upload_payload(), {'token_file': ('x.png', io.BytesIO(b'abc'))})
        self.assertEqual(response.status_code, 403)
        self.assert_no_upstream()

    async def test_meta_async_report_id_is_authorized_for_poll(self):
        self.reply = {'report_run_id': '777'}
        response = await self.post(self.body(method='POST', path='act_123/insights', query={},
            body={'fields': 'spend', 'async': 'true'}, idempotency_key='report-start'))
        self.assertEqual(response.status_code, 200, response.text)
        self.state.require_object('meta', '777', '123', 'report')
        self.reply = {'id': '777', 'async_status': 'Job Completed'}
        response = await self.post(self.body(path='777', query={'fields': 'async_status'}))
        self.assertEqual(response.status_code, 200)

    async def test_meta_update_delete_success_and_unknown_response(self):
        self.state.bind_object('meta', '456', '123', 'campaign')
        self.reply = {'success': True}
        for method in ('POST', 'DELETE'):
            r = await self.post(self.body(path='456', method=method, query={}, body={'status': 'PAUSED'} if method == 'POST' else None,
                idempotency_key='mutation-' + method))
            self.assertEqual(r.json()['write_outcome'], 'confirmed_succeeded')
        self.reply = {}
        r = await self.post(self.body(path='456', method='POST', query={}, body={'name': 'new'}, idempotency_key='unknown-update'))
        self.assertEqual(r.status_code, 502)
        r = await self.post(self.body(path='456', method='POST', query={}, body={'name': 'new'}, idempotency_key='unknown-update'))
        self.assertEqual(r.status_code, 409)

    async def test_graph_nested_ad_relationship_allowed_alias_rejected(self):
        r = await self.post(self.body(query={'fields': 'id,creative{id,name,object_story_spec},adset{id,name}'}))
        self.assertEqual(r.status_code, 200, r.text)
        count = len(self.seen)
        for fields in ('id,business{id}', 'id,accounts{id}', 'creative.as(id){name}', 'creative{accounts{id}}', 'id,'):
            r = await self.post(self.body(query={'fields': fields}))
            self.assertIn(r.status_code, (400, 403))
        self.assertEqual(count, len(self.seen))

    async def test_nested_foreign_advertiser_rejected(self):
        r = await self.post(self.body(platform='tiktok', method='POST', path='campaign/create/', query={},
            body={'advertiser_id': '123', 'extra': {'advertiser_id': '999'}}, idempotency_key='cross'))
        self.assertEqual(r.status_code, 403)
        self.assert_no_upstream()

    async def test_new_derived_secret_echo_blocked(self):
        self.reply = {'data': [{'access_token': 'NEW_DERIVED_SECRET', 'name': 'NEW_DERIVED_SECRET'}]}
        r = await self.post()
        self.assertEqual(r.status_code, 502)
        self.assertNotIn('NEW_DERIVED_SECRET', r.text)

    async def test_multi_account_auth_template_not_reused_wrongly(self):
        from motata_cli.common.auth import resolve_auth
        from motata_cli.init_profile import resolve_default_access_token
        with patch.dict(os.environ, {'MOTATA_AUTH_MODE': 'gateway'}, clear=True):
            template, _ = resolve_default_access_token('meta')
            for account in ('123', '456'):
                result = resolve_auth(account_id=account, access_token=template)
                self.assertEqual(result.access_token.account_id, account)
            with self.assertRaises(CliError):
                resolve_auth(account_id='456', access_token=GatewayAuthRef('meta', '123'))

    async def test_diagnostic_redaction_accepts_safe_reference(self):
        from motata_cli.common.security import redact
        self.assertEqual(redact('safe diagnostic', (GatewayAuthRef('meta'),)), 'safe diagnostic')

# Do not rerun the inherited suite as new test counts.
for name in base.GatewayTests.__dict__:
    if name.startswith('test_') and name not in ContinuationTests.__dict__:
        setattr(ContinuationTests, name, None)
