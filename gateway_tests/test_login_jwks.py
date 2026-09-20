from __future__ import annotations
import asyncio
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from motata_cli.transport.login import LoginConfig, SessionStore, DeviceLogin
from motata_cli.common.errors import CliError
from motata_gateway.jwks import RemoteJWTVerifier
from motata_gateway.errors import GatewayError


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SessionStore(self.root / 'session.json')
        self.config = LoginConfig('https://auth.example', 'https://gateway.example', 'motata-cli',
                                  'https://auth.example/device', 'https://auth.example/token')
        self.now = 1000
        self.sleeps, self.calls, self.responses = [], [], []
        outer = self
        class Session:
            trust_env = True
            def post(self, url, **kw):
                outer.calls.append((url, kw['data']))
                value = outer.responses.pop(0)
                if isinstance(value, Exception):
                    raise value
                status, data = value
                response = requests.Response(); response.status_code = status
                response._content = json.dumps(data).encode(); response._content_consumed = True
                return response
            def close(self): pass
        def sleep(seconds):
            self.sleeps.append(seconds); self.now += seconds
        self.client = DeviceLogin(self.config, self.store, session=Session(), clock=lambda: self.now, sleep=sleep)

    def tearDown(self): self.temp.cleanup()

    def tokens(self, refresh='ROTATING_REFRESH_A'):
        return {'access_token': 'opaque.gateway.jwt', 'refresh_token': refresh,
                'token_type': 'Bearer', 'expires_in': 600}

    def device(self):
        return {'device_code': 'PRIVATE_DEVICE_CODE', 'user_code': 'ABCD-EFGH',
                'verification_uri': 'https://auth.example/activate', 'expires_in': 100, 'interval': 1}

    def test_device_pending_and_slow_down_then_login(self):
        self.responses = [(200, self.device()), (400, {'error': 'authorization_pending'}),
                          (400, {'error': 'slow_down'}), (200, self.tokens())]
        displayed = []
        self.client.login(lambda *args: displayed.append(args))
        self.assertEqual(self.sleeps, [1, 1, 6])
        self.assertEqual(displayed, [('https://auth.example/activate', 'ABCD-EFGH')])
        self.assertNotIn('PRIVATE_DEVICE_CODE', repr(displayed))
        self.assertEqual(self.client.access_token(), 'opaque.gateway.jwt')
        self.assertEqual(self.store.path.stat().st_mode & 0o077, 0)

    def test_device_denied_does_not_poll_again(self):
        self.responses = [(200, self.device()), (400, {'error': 'access_denied'})]
        with self.assertRaises(CliError): self.client.login(lambda *x: None)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.store.path.exists())

    def test_device_expiry_bounded(self):
        device = self.device(); device['expires_in'] = 3
        self.responses = [(200, device), (400, {'error': 'authorization_pending'}), (400, {'error': 'authorization_pending'})]
        with self.assertRaises(CliError): self.client.login(lambda *x: None)
        self.assertEqual(len(self.calls), 3)

    def test_device_bad_verification_host_never_displayed(self):
        device = self.device(); device['verification_uri'] = 'https://evil.example/login'
        self.responses = [(200, device)]
        shown = []
        with self.assertRaises(CliError): self.client.login(lambda *x: shown.append(x))
        self.assertEqual(shown, [])

    def test_fixed_endpoints_reject_cross_origin_and_plaintext(self):
        for endpoint in ('http://auth.example/token', 'https://evil.example/token', 'https://u:p@auth.example/token'):
            with self.assertRaises(ValueError):
                LoginConfig('https://auth.example', 'https://gateway.example', 'cli', 'https://auth.example/device', endpoint)

    def test_refresh_rotation_persisted(self):
        self.client.save_tokens(self.tokens())
        self.now += 590
        self.responses = [(200, self.tokens('ROTATING_REFRESH_B'))]
        self.assertEqual(self.client.access_token(), 'opaque.gateway.jwt')
        self.assertEqual(self.store.read()['refresh_token'], 'ROTATING_REFRESH_B')
        self.assertEqual(self.calls[0][1]['refresh_token'], 'ROTATING_REFRESH_A')

    def test_refresh_timeout_never_reuses_old_credential(self):
        self.client.save_tokens(self.tokens()); self.now += 590
        self.responses = [requests.Timeout('PRIVATE_RESPONSE_SECRET')]
        for _ in range(2):
            with self.assertRaises(CliError) as caught: self.client.access_token()
            self.assertNotIn('PRIVATE_RESPONSE_SECRET', str(caught.exception))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store.read()['status'], 'refresh_pending')
        self.assertNotIn('ROTATING_REFRESH_A', self.store.path.read_text())

    def test_refresh_requires_new_refresh_credential(self):
        self.client.save_tokens(self.tokens()); self.now += 590
        self.responses = [(200, self.tokens())]
        with self.assertRaises(CliError): self.client.access_token()
        self.assertEqual(self.store.read()['status'], 'refresh_pending')

    def test_concurrent_refresh_only_once(self):
        self.client.save_tokens(self.tokens()); self.now += 590
        self.responses = [(200, self.tokens('ROTATING_REFRESH_B'))]
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(lambda _: self.client.access_token(), range(10)))
        self.assertEqual(results, ['opaque.gateway.jwt'] * 10)
        self.assertEqual(len(self.calls), 1)

    def test_issuer_binding_change_cannot_forward_refresh(self):
        self.client.save_tokens(self.tokens())
        self.client.config = LoginConfig('https://different.example', 'https://gateway.example', 'cli',
                                         'https://different.example/device', 'https://different.example/token')
        with self.assertRaises(CliError): self.client.access_token()
        self.assertEqual(self.calls, [])

    def test_login_invalid_lifetime_scope_and_refresh_rejected(self):
        for changed in ({'expires_in': 901}, {'expires_in': True}, {'scope': 'gateway:use admin:all'},
                        {'refresh_token': 'embedded\nsecret'}, {'token_type': 'id_token'}):
            with self.assertRaises(CliError): self.client.save_tokens({**self.tokens(), **changed})

    def test_local_logout_does_not_claim_remote_revocation(self):
        self.client.save_tokens(self.tokens()); self.store.logout()
        with self.assertRaises(CliError): self.client.access_token()
        self.assertNotIn('ROTATING_REFRESH_A', self.store.path.read_text())

    def test_private_session_symlink_rejected(self):
        target = self.root / 'target'; target.write_text('{}')
        self.store.path.symlink_to(target)
        with self.assertRaises(CliError): self.store.read()


class OnlineJWKSTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key()))
        public.update(kid='one', alg='ES256', use='sig', key_ops=['verify'])
        self.keys = [public]; self.calls = []; self.now = 1000; self.status = 200
        async def request(req):
            self.calls.append(req)
            await asyncio.sleep(.001)
            return httpx.Response(self.status, json={'keys': self.keys})
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(request))
        self.verifier = RemoteJWTVerifier(issuer='https://auth.example', audience='motata-gateway',
            jwks_url='https://auth.example/keys', http=self.http, clock=lambda: self.now, cache_ttl=10)

    async def asyncTearDown(self): await self.http.aclose()

    def token(self, *, kid='one', extra=None):
        now = int(time.time())
        claims = {'iss': 'https://auth.example', 'aud': 'motata-gateway', 'iat': now, 'exp': now + 600,
                  'sub': 'agent', 'client_id': 'cli', 'workspace_id': 'single', 'sid': 'session', 'jti': 'id',
                  'scope': 'gateway:use meta:read'}
        return jwt.encode(claims, self.private, algorithm='ES256', headers={'kid': kid, 'typ': 'at+jwt', **(extra or {})})

    async def test_50_verifications_one_jwks_fetch(self):
        results = await asyncio.gather(*(self.verifier.verify_async(self.token()) for _ in range(50)))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(x.subject == 'agent' for x in results))
        self.assertNotIn('authorization', self.calls[0].headers)

    async def test_unknown_kid_cooldown(self):
        await self.verifier.verify_async(self.token())
        for _ in range(10):
            with self.assertRaises(GatewayError): await self.verifier.verify_async(self.token(kid='unknown'))
        self.assertEqual(len(self.calls), 1)

    async def test_key_retirement_after_ttl(self):
        await self.verifier.verify_async(self.token())
        self.keys = [{**self.keys[0], 'kid': 'two'}]; self.now += 11
        with self.assertRaises(GatewayError): await self.verifier.verify_async(self.token())
        principal = await self.verifier.verify_async(self.token(kid='two'))
        self.assertEqual(principal.key_id, 'two')
        self.assertEqual(len(self.calls), 2)

    async def test_expired_cache_failure_never_uses_stale_key(self):
        await self.verifier.verify_async(self.token())
        self.now += 11; self.status = 503
        for _ in range(3):
            with self.assertRaises(GatewayError) as caught: await self.verifier.verify_async(self.token())
            self.assertEqual(caught.exception.status, 503)
        self.assertEqual(len(self.calls), 2)

    async def test_private_key_response_rejected(self):
        self.keys[0]['d'] = 'private-material'
        with self.assertRaises(GatewayError): await self.verifier.verify_async(self.token())
        self.assertEqual(self.verifier._keys, {})

    async def test_jku_never_causes_network(self):
        with self.assertRaises(GatewayError):
            await self.verifier.verify_async(self.token(extra={'jku': 'https://evil.example'}))
        self.assertEqual(self.calls, [])

    async def test_jwks_redirect_never_followed(self):
        self.status = 302
        with self.assertRaises(GatewayError): await self.verifier.verify_async(self.token())
        self.assertEqual(len(self.calls), 1)

    async def test_invalid_jwks_config_rejected(self):
        for url in ('http://auth.example/keys', 'https://u:p@auth.example/keys', 'https://auth.example/keys?secret=x'):
            with self.assertRaises(ValueError):
                RemoteJWTVerifier(issuer='https://auth.example', audience='gw', jwks_url=url, http=self.http)
