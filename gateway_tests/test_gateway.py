"""Offline JWT, authorization, credential boundary, replay and 50-concurrency tests."""
from __future__ import annotations
import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from motata_gateway.jwt_auth import JWTVerifier
from motata_gateway.state import GatewayState
from motata_gateway.service import GatewayService
from motata_gateway.server import GatewayApp
from motata_gateway.limits import Limits
from motata_gateway.protocol import PlatformRequest
from motata_gateway.errors import GatewayError


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.private = ec.generate_private_key(ec.SECP256R1())
        public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key()))
        public.update(kid='test-key', alg='ES256', use='sig', key_ops=['verify'])
        self.jwks = self.root / 'jwks.json'
        self.jwks.write_text(json.dumps({'keys': [public]}))
        self.meta_secret = 'SECRET_CANARY_META_' + uuid.uuid4().hex
        self.tiktok_secret = 'SECRET_CANARY_TIKTOK_' + uuid.uuid4().hex
        self.state = GatewayState(self.root / 'private', create=True)
        self.state.put_credential('meta-main', 'meta', self.meta_secret)
        self.state.put_credential('tiktok-main', 'tiktok', self.tiktok_secret)
        for platform in ('meta', 'tiktok'):
            self.state.grant(subject='agent-a', client='motata-cli', workspace='single-user',
                             platform=platform, account='123', reference=platform + '-main')
        self.verifier = JWTVerifier(issuer='https://auth.example.com', audience='motata-gateway', jwks_path=self.jwks)
        self.seen = []
        self.delay = 0
        self.reply = {'data': [{'id': '456', 'name': 'business data', 'status': 'PAUSED'}]}
        self.http_status = 200
        self.handler_error = None
        self.start = None
        self.release = None
        self.upstream_active = 0
        self.upstream_peak = 0
        async def upstream(request):
            self.seen.append(request)
            self.upstream_active += 1
            self.upstream_peak = max(self.upstream_peak, self.upstream_active)
            try:
                if self.start:
                    self.start.set()
                if self.release:
                    await self.release.wait()
                await asyncio.sleep(self.delay)
                if self.handler_error:
                    raise self.handler_error
                return httpx.Response(self.http_status, json=self.reply,
                                      headers={'Authorization': 'upstream-secret-header', 'Set-Cookie': 'sensitive-cookie'})
            finally:
                self.upstream_active -= 1
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream), trust_env=False)
        self.service = GatewayService(verifier=self.verifier, state=self.state, meta_version='v23.0', http=self.http)
        self.app = GatewayApp(self.service)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='https://gateway.example.com')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.http.aclose()
        self.directory.cleanup()

    def claims(self):
        now = int(time.time())
        return {'iss': 'https://auth.example.com', 'aud': 'motata-gateway', 'sub': 'agent-a',
                'client_id': 'motata-cli', 'workspace_id': 'single-user', 'sid': 'session-a',
                'jti': 'test-jti', 'iat': now, 'exp': now + 600,
                'scope': 'gateway:use meta:read meta:write tiktok:read'}

    def token(self, *, claims=None, headers=None, private=None):
        return jwt.encode(claims or self.claims(), private or self.private, algorithm='ES256',
                          headers={'kid': 'test-key', 'typ': 'at+jwt', **(headers or {})})

    def body(self, **overrides):
        return {'protocol': 'motata-gateway/v1', 'request_id': str(uuid.uuid4()), 'platform': 'meta',
                'account_id': '123', 'method': 'GET', 'path': 'act_123/campaigns',
                'query': {'fields': 'id,name,status', 'limit': 25}, **overrides}

    async def post(self, body=None, token=None):
        return await self.client.post('/v1/platform/request', json=body or self.body(),
                                      headers={'Authorization': 'Bearer ' + (token or self.token())})

    def assert_no_upstream(self):
        self.assertEqual(len(self.seen), 0)
        self.assertEqual(self.state.resolve_count, 0)

    async def test_meta_read_and_injection_only_on_upstream(self):
        token = self.token()
        response = await self.post(token=token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['data'], self.reply)
        self.assertEqual(self.seen[0].headers['authorization'], 'Bearer ' + self.meta_secret)
        self.assertNotIn(token, str(self.seen[0].headers))
        self.assertNotIn('access_token', str(self.seen[0].url))
        self.assertNotIn(self.meta_secret, response.text)
        self.assertNotIn('upstream-secret-header', response.text)
        self.assertNotIn('set-cookie', response.headers)

    async def test_tiktok_read_and_advertiser_injection(self):
        response = await self.post(self.body(platform='tiktok', path='campaign/get/', query={'advertiser_id': '123'}))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.seen[0].headers['access-token'], self.tiktok_secret)
        self.assertNotIn('authorization', self.seen[0].headers)

    async def test_missing_authorization_before_json(self):
        response = await self.client.post('/v1/platform/request', content=b'not JSON')
        self.assertEqual(response.status_code, 401)
        self.assert_no_upstream()

    async def test_jwt_negative_matrix(self):
        invalid = []
        for name in ('iss', 'aud', 'sub', 'exp', 'iat', 'jti', 'client_id', 'sid', 'workspace_id', 'scope'):
            data = self.claims(); del data[name]
            invalid.append(('missing ' + name, self.token(claims=data)))
        for name, value in (('iss', 'https://evil.example.com'), ('aud', 'another-service'),
                            ('exp', int(time.time()) - 61), ('iat', int(time.time()) + 120),
                            ('nbf', int(time.time()) + 120), ('exp', int(time.time()) + 1800),
                            ('iat', True), ('exp', str(int(time.time()) + 600)),
                            ('scope', ['gateway:use']), ('sub', ''), ('jti', ''),
                            ('aud', ['motata-gateway', 'other']), ('scope', 'gateway:use  meta:read')):
            data = self.claims(); data[name] = value
            invalid.append((name + '=' + str(value), self.token(claims=data)))
        for headers in ({'kid': 'unknown'}, {'kid': '../path'}, {'typ': 'JWT'},
                        {'jku': 'https://attacker.example/jwks'}, {'x5u': 'https://attacker.example/key'},
                        {'jwk': {}}, {'crit': ['extension']}, {'x5c': []}):
            invalid.append((str(headers), self.token(headers=headers)))
        invalid.append(('wrong-signature', self.token(private=ec.generate_private_key(ec.SECP256R1()))))
        invalid.append(('HS256', jwt.encode(self.claims(), 'test-only-hmac-key-not-used-by-gateway', algorithm='HS256', headers={'kid': 'test-key', 'typ': 'at+jwt'})))
        invalid.append(('none', jwt.encode(self.claims(), key='', algorithm='none', headers={'kid': 'test-key', 'typ': 'at+jwt'})))
        invalid.append(('malformed', 'bad.jwt.string'))
        for label, token in invalid:
            with self.subTest(label=label):
                response = await self.post(token=token)
                self.assertEqual(response.status_code, 401, response.text)
                self.assertNotIn(token, response.text)
                self.assert_no_upstream()

    async def test_valid_jwt_wrong_scope_or_grant_never_resolves_token(self):
        for field, value in (('scope', 'gateway:use'), ('scope', 'meta:read'),
                             ('sub', 'another-agent'), ('workspace_id', 'another-workspace'),
                             ('client_id', 'another-client')):
            data = self.claims(); data[field] = value
            response = await self.post(token=self.token(claims=data))
            self.assertEqual(response.status_code, 403, response.text)
            self.assert_no_upstream()

    async def test_revocations_are_checked_per_request(self):
        response = await self.post()
        self.assertEqual(response.status_code, 200)
        calls = len(self.seen)
        self.state.revoke('sid', 'session-a')
        response = await self.post()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.seen), calls)
        self.assertEqual(self.state.resolve_count, 1)

    async def test_sub_kid_jti_revocations(self):
        for kind, value in (('sub', 'agent-a'), ('kid', 'test-key'), ('jti', 'test-jti')):
            with self.subTest(kind=kind):
                self.state.revoke(kind, value)
                response = await self.post()
                self.assertEqual(response.status_code, 401)
                self.assert_no_upstream()
                with self.state.connection() as db:
                    db.execute('DELETE FROM revoked')

    async def test_grant_removal_effective_without_jwt_expiry(self):
        self.state.remove_grant(subject='agent-a', client='motata-cli', workspace='single-user', platform='meta', account='123')
        response = await self.post()
        self.assertEqual(response.status_code, 403)
        self.assert_no_upstream()

    async def test_jti_is_not_a_single_use_token(self):
        token = self.token()
        for _ in range(3):
            self.assertEqual((await self.post(token=token)).status_code, 200)
        self.assertEqual(len(self.seen), 3)

    async def test_client_cannot_choose_credential(self):
        response = await self.post(self.body(credential_ref='tiktok-main'))
        self.assertEqual(response.status_code, 403)
        self.assert_no_upstream()

    async def test_cross_account_path_denied(self):
        response = await self.post(self.body(path='act_456/campaigns'))
        self.assertEqual(response.status_code, 403)
        self.assert_no_upstream()

    async def test_cross_advertiser_query_denied(self):
        response = await self.post(self.body(platform='tiktok', path='campaign/get/', query={'advertiser_id': '456'}))
        self.assertEqual(response.status_code, 403)
        self.assert_no_upstream()

    async def test_unverified_object_is_denied_before_credential_lookup(self):
        response = await self.post(self.body(path='456'))
        self.assertEqual(response.status_code, 403)
        self.assert_no_upstream()

    async def test_list_evidence_binds_object_to_account(self):
        await self.post()
        response = await self.post(self.body(path='456'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.seen), 2)

    async def test_request_attack_matrix(self):
        variants = [self.body(path=path) for path in ('https://evil.example/x', '//evil/x', '/x', 'act_123/../oauth',
                     'act_123/%2e%2e/oauth', 'act_123\\campaigns', 'act_123/campaigns?access_token=x', 'act_123/campaigns#x')]
        variants += [self.body(**{k: v}) for k, v in (('headers', {'Authorization': 'bad'}), ('url', 'https://evil.example'),
                                                     ('admin', True), ('query', {'access_token': 'bad'}),
                                                     ('query', {'fields': 'id,accounts{access_token}'}),
                                                     ('query', {'filtering': '{"access_token":"bad"}'}),
                                                     ('query', {'method': 'POST'}), ('query', {'batch': []}),
                                                     ('query', {'ids': '999'}), ('query', {'fields': 'campaigns.as(id)'}))]
        variants.append(self.body(platform='tiktok', path='report/integrated/get/',
                                  query={'advertiser_id': '123', 'advertiser_ids': ['999']}))
        for body in variants:
            with self.subTest(body=body):
                response = await self.post(body)
                self.assertIn(response.status_code, (400, 403), response.text)
                self.assert_no_upstream()

    async def test_unknown_oauth_endpoint_denied(self):
        response = await self.post(self.body(path='oauth/access_token'))
        self.assertEqual(response.status_code, 403)
        self.assert_no_upstream()

    async def test_success_secrets_removed_recursively(self):
        self.reply = {'data': [{'id': '456', 'access_token': 'DERIVED_SECRET_NEVER_SENT',
                               'nested': {'refresh_token': 'ANOTHER_DERIVED_SECRET', 'safe': True}}]}
        response = await self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data'], {'data': [{'id': '456', 'nested': {'safe': True}}]})

    async def test_secret_in_business_string_blocks_entire_response(self):
        for value in (self.meta_secret, base64.b64encode(self.meta_secret.encode()).decode(),
                      'https://example.com/?unusual=' + self.meta_secret):
            self.reply = {'data': [{'name': value}]}
            response = await self.post()
            self.assertEqual(response.status_code, 502)
            self.assertEqual(response.json()['error']['code'], 'RESPONSE_BLOCKED')
            self.assertNotIn(value, response.text)

    async def test_nested_json_success_is_sanitized(self):
        self.reply = {'data': [], 'nested': json.dumps({'access_token': 'DERIVED', 'safe': 1})}
        response = await self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.json()['data']['nested']), {'safe': 1})

    async def test_upstream_error_message_cannot_echo_token(self):
        self.reply = {'error': {'code': 190, 'message': self.meta_secret}}
        self.http_status = 400
        response = await self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(self.meta_secret, response.text)

    async def test_redirects_never_followed(self):
        self.http_status = 302
        response = await self.post()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(response.json()['error']['code'], 'UPSTREAM_REDIRECT_BLOCKED')

    async def test_pagination_is_opaque_and_account_bound(self):
        self.reply = {'data': [], 'paging': {'next': 'https://graph.facebook.com/v23.0/act_123/campaigns?after=abc&access_token=' + self.meta_secret}}
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        ref = response.json()['data']['paging']['next']
        self.assertTrue(ref.startswith('motata-page:'))
        self.assertNotIn(self.meta_secret, repr(self.service.pages.entries))
        self.reply = {'data': []}
        page = await self.client.post('/v1/pages/' + ref[12:], json={}, headers={'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(self.seen[-1].url.params['after'], 'abc')
        data = self.claims(); data['sid'] = 'different-session'
        page = await self.client.post('/v1/pages/' + ref[12:], json={}, headers={'Authorization': 'Bearer ' + self.token(claims=data)})
        self.assertEqual(page.status_code, 404)

    async def test_bad_pagination_destination_blocked(self):
        for url in ('https://evil.example/x', 'https://graph.facebook.com/v23.0/act_456/campaigns'):
            self.reply = {'paging': {'next': url}}
            response = await self.post()
            self.assertEqual(response.status_code, 502)

    async def test_write_requires_idempotency_key_before_credentials(self):
        response = await self.post(self.body(method='POST', query={}, body={'name': 'canary', 'status': 'PAUSED'}))
        self.assertEqual(response.status_code, 400)
        self.assert_no_upstream()

    async def test_write_idempotent_result_replay_not_platform_replay(self):
        self.reply = {'id': '789'}
        body = self.body(method='POST', query={}, body={'name': 'canary', 'status': 'PAUSED'}, idempotency_key='job-1')
        first = await self.post(body)
        second = await self.post({**body, 'request_id': str(uuid.uuid4())})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['write_outcome'], 'confirmed_succeeded')
        self.assertTrue(second.json()['replayed'])
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.state.resolve_count, 1)
        changed = await self.post({**body, 'body': {'name': 'changed'}})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(len(self.seen), 1)

    async def test_unknown_write_is_not_resent(self):
        self.handler_error = httpx.ReadTimeout('fake transport exception containing ' + self.meta_secret)
        body = self.body(method='POST', query={}, body={'name': 'canary'}, idempotency_key='uncertain-1')
        first = await self.post(body)
        second = await self.post(body)
        self.assertEqual(first.status_code, 502)
        self.assertEqual(first.json()['error']['write_outcome'], 'unknown')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()['error']['code'], 'WRITE_NEEDS_REVIEW')
        self.assertEqual(len(self.seen), 1)
        self.assertNotIn(self.meta_secret, first.text)

    async def test_known_platform_failure_result_is_cached_not_retried(self):
        self.reply = {'error': {'code': 100, 'message': 'invalid name'}}
        self.http_status = 400
        body = self.body(method='POST', query={}, body={'name': 'canary'}, idempotency_key='failed-1')
        first, second = await self.post(body), await self.post(body)
        self.assertEqual(first.json()['write_outcome'], 'confirmed_failed')
        self.assertTrue(second.json()['replayed'])
        self.assertEqual(len(self.seen), 1)

    async def test_encrypted_state_does_not_contain_plaintext_tokens(self):
        await self.post()
        for path in (self.root / 'private').iterdir():
            if path.name != 'master.key':
                self.assertNotIn(self.meta_secret.encode(), path.read_bytes())
                self.assertNotIn(self.tiktok_secret.encode(), path.read_bytes())
        self.assertNotIn(self.meta_secret, repr(self.state.resolve('meta-main', 'meta')))

    async def test_private_file_permissions_enforced(self):
        if os.name == 'nt':
            self.skipTest('POSIX permission gate; Windows ACL validation is a release blocker')
        self.state.key_path.chmod(0o644)
        with self.assertRaises(ValueError):
            GatewayState(self.root / 'private')
        self.state.key_path.chmod(0o600)

    async def test_50_concurrent_reads_are_bounded(self):
        self.delay = .02
        self.reply = {'data': []}
        for i in range(25):
            self.state.grant(subject='agent-a', client='motata-cli', workspace='single-user', platform='meta',
                             account=str(1000 + i), reference='meta-main')
        token = self.token()
        responses = await asyncio.gather(*[
            self.post(self.body(account_id=str(1000 + i % 25), path=f'act_{1000 + i % 25}/campaigns'), token)
            for i in range(50)])
        self.assertTrue(all(r.status_code == 200 for r in responses), [r.text for r in responses if r.status_code != 200])
        self.assertEqual(len(self.seen), 50)
        self.assertLessEqual(self.upstream_peak, 20)
        self.assertGreater(self.upstream_peak, 1)
        self.assertEqual(self.service.scheduler.admitted, 0)
        self.assertEqual(self.service.scheduler.active, 0)
        self.assertEqual(len(self.service.scheduler.accounts), 0)

    async def test_same_account_limited_to_two(self):
        self.delay = .005
        responses = await asyncio.gather(*[self.post() for _ in range(10)])
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertLessEqual(self.upstream_peak, 2)

    async def test_jwks_rotation_and_old_key_retirement(self):
        other = ec.generate_private_key(ec.SECP256R1())
        public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(other.public_key()))
        public.update(kid='new-key', alg='ES256', use='sig', key_ops=['verify'])
        old = json.loads(self.jwks.read_text())['keys'][0]
        self.jwks.write_text(json.dumps({'keys': [old, public]}))
        response = await self.post(token=self.token(private=other, headers={'kid': 'new-key'}))
        self.assertEqual(response.status_code, 200)
        self.jwks.write_text(json.dumps({'keys': [public]}))
        response = await self.post()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.seen), 1)

    async def test_private_jwk_rejected(self):
        data = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.private))
        data.update(kid='test-key', alg='ES256')
        self.jwks.write_text(json.dumps({'keys': [data]}))
        self.assertEqual((await self.post()).status_code, 401)
        self.assert_no_upstream()

    async def test_http_without_development_mode_rejected(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://gateway.example.com') as client:
            response = await client.post('/v1/platform/request', json=self.body(), headers={'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(response.status_code, 400)
        self.assert_no_upstream()

    async def test_duplicate_json_keys_rejected(self):
        raw = '{"account_id":"123","account_id":"456"}'
        response = await self.client.post('/v1/platform/request', content=raw, headers={'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(response.status_code, 400)
        self.assert_no_upstream()

    async def test_queue_timeout_is_bounded_and_releases_slots(self):
        from motata_gateway.limits import Scheduler
        self.service.scheduler = Scheduler(Limits(admitted=4, upstream=1, per_account=1, queue_wait=.01))
        self.delay = .05
        a, b = await asyncio.gather(self.post(), self.post())
        self.assertEqual(sorted([a.status_code, b.status_code]), [200, 503])
        self.assertEqual(self.service.scheduler.active, 0)
        self.assertEqual(self.service.scheduler.admitted, 0)
        self.assertEqual(len(self.seen), 1)

    async def test_admission_overflow_rejects_instead_of_growing_queue(self):
        from motata_gateway.limits import Scheduler
        self.service.scheduler = Scheduler(Limits(admitted=2, upstream=1, per_account=1))
        self.start, self.release = asyncio.Event(), asyncio.Event()
        first = asyncio.create_task(self.post())
        await self.start.wait()
        second = asyncio.create_task(self.post())
        await asyncio.sleep(.01)
        third = await self.post()
        self.assertEqual(third.status_code, 503)
        self.assertEqual(third.json()['error']['code'], 'GATEWAY_BUSY')
        self.release.set()
        await asyncio.gather(first, second)
        self.assertEqual(self.service.scheduler.admitted, 0)

    async def test_grant_rechecked_after_wait(self):
        from motata_gateway.limits import Scheduler
        self.service.scheduler = Scheduler(Limits(admitted=4, upstream=1, per_account=1))
        self.start, self.release = asyncio.Event(), asyncio.Event()
        first = asyncio.create_task(self.post())
        await self.start.wait()
        second = asyncio.create_task(self.post())
        await asyncio.sleep(.01)
        self.state.remove_grant(subject='agent-a', client='motata-cli', workspace='single-user', platform='meta', account='123')
        self.release.set()
        a, b = await asyncio.gather(first, second)
        self.assertEqual(a.status_code, 200)
        self.assertEqual(b.status_code, 403)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.state.resolve_count, 1)

if __name__ == '__main__':
    unittest.main()


class CLIIntegrationTests(GatewayTests):
    """Actual argparse handlers -> HTTPS transport -> ASGI gateway -> mock upstream."""
    # Run only integration additions here, not the entire parent suite a second time.
    async def run_cli(self, argv, *, extra_env=None):
        import io
        import requests
        from contextlib import redirect_stdout, redirect_stderr
        from motata_cli.__main__ import main
        from motata_cli.transport.gateway import RemoteGatewayTransport
        loop = asyncio.get_running_loop()
        remote = self.client
        captured = []
        class Session:
            trust_env = False
            def post(self, url, **kwargs):
                captured.append(kwargs['json'])
                response = asyncio.run_coroutine_threadsafe(remote.post(
                    url, json=kwargs['json'], headers=kwargs['headers']), loop).result(10)
                converted = requests.Response()
                converted.status_code = response.status_code
                converted._content = response.content
                converted._content_consumed = True
                converted.headers.update(response.headers)
                return converted
            def close(self):
                pass
        def execute():
            stdout, stderr = io.StringIO(), io.StringIO()
            env = {'MOTATA_AUTH_MODE': 'gateway', 'MOTATA_GATEWAY_URL': 'https://gateway.example.com',
                   'MOTATA_GATEWAY_JWT': self.token(), 'MOTATA_GATEWAY_META_ACCOUNT_ID': '123',
                   'MOTATA_GATEWAY_TIKTOK_ACCOUNT_ID': '123', 'MOTATA_SUPPRESS_SKILLS_NOTICE': '1'}
            env.update(extra_env or {})
            transport = RemoteGatewayTransport('https://gateway.example.com', session=Session())
            with patch.dict(os.environ, env, clear=True), redirect_stdout(stdout), redirect_stderr(stderr), \
                 patch('motata_cli.transport.gateway.RemoteGatewayTransport.from_environment', return_value=transport), \
                 patch('motata_cli.__main__.ensure_dirs'), patch('motata_cli.__main__.maybe_emit_skills_drift_notice'), \
                 patch('requests.get', side_effect=AssertionError('Direct platform GET forbidden')), \
                 patch('requests.post', side_effect=AssertionError('Direct platform POST forbidden')), \
                 patch('requests.request', side_effect=AssertionError('Direct platform request forbidden')):
                code = main(argv)
            return code, stdout.getvalue(), stderr.getvalue()
        result = await asyncio.to_thread(execute)
        self.assertNotIn(self.meta_secret, repr(captured))
        self.assertNotIn(self.tiktok_secret, repr(captured))
        return result

    async def test_cli_meta_list(self):
        code, output, error = await self.run_cli(['meta', 'campaigns', 'list', '--account', '123'])
        self.assertEqual(code, 0, error)
        self.assertEqual(json.loads(output), self.reply['data'])
        self.assertEqual(len(self.seen), 1)

    async def test_cli_meta_create_uses_stable_write_key(self):
        self.reply = {'id': '789'}
        argv = ['meta', 'campaigns', 'create', '--account', '123', '--name', 'Canary', '--objective', 'OUTCOME_TRAFFIC']
        for _ in range(2):
            code, output, error = await self.run_cli(argv, extra_env={'MOTATA_GATEWAY_IDEMPOTENCY_KEY': 'cli-write-1'})
            self.assertEqual(code, 0, error)
            self.assertEqual(json.loads(output), {'id': '789'})
        self.assertEqual(len(self.seen), 1)
        self.assertIn(b'PAUSED', self.seen[0].content)

    async def test_cli_tiktok_list_avoids_sdk_token_runtime(self):
        self.reply = {'code': 0, 'data': {'list': [{'campaign_id': '456', 'campaign_name': 'test'}]}}
        code, output, error = await self.run_cli(['tiktok', 'campaigns', 'list', '--advertiser-id', '123'])
        self.assertEqual(code, 0, error)
        self.assertIn('456', output)
        self.assertEqual(len(self.seen), 1)

    async def test_cli_unmigrated_command_fails_closed(self):
        code, output, error = await self.run_cli(['init'])
        self.assertEqual(code, 1)
        self.assertIn('GATEWAY_OPERATION_UNAVAILABLE', error)
        self.assert_no_upstream()

    async def test_cli_direct_token_flag_is_rejected_without_echo(self):
        code, output, error = await self.run_cli(['meta', 'campaigns', 'list', '--account', '123', '--access-token', 'NOT_A_REAL_SECRET'])
        self.assertEqual(code, 2)
        self.assertNotIn('NOT_A_REAL_SECRET', output + error)
        self.assert_no_upstream()

    async def test_cli_refuses_inherited_platform_credential(self):
        code, output, error = await self.run_cli(['meta', 'campaigns', 'list', '--account', '123'],
                                               extra_env={'META_ACCESS_TOKEN': 'INHERITED_CANARY'})
        self.assertEqual(code, 2)
        self.assertNotIn('INHERITED_CANARY', output + error)
        self.assert_no_upstream()

# unittest also discovers inherited test methods. Avoid running the common suite twice.
for _name in list(GatewayTests.__dict__):
    if _name.startswith('test_') and _name not in CLIIntegrationTests.__dict__:
        setattr(CLIIntegrationTests, _name, None)
