"""Offline current-OpenAPI contract, isolation, cache, lifecycle and race tests."""
from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from motata_gateway.admin import main as admin_main
from motata_gateway.auth_center import (AuthCenterBinding, AuthCenterProfile, AuthCenterProvider,
                                        LeaseCache, load_profiles, private_bytes)
from motata_gateway.credentials import CredentialResolver
from motata_gateway.errors import GatewayError
from motata_gateway.jwt_auth import Principal
from motata_gateway.state import CredentialLease, GatewayState

TENANT = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
API_KEY = 'ak_' + TENANT + '_SECRET_CANARY_AUTH_CENTER_0123456789'
PLATFORM_TOKEN = 'SECRET_CANARY_EXTERNAL_META_TOKEN_0123456789'


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key_file = self.root / 'service.key'
        self.key_file.write_text(API_KEY)
        self.key_file.chmod(0o600)
        self.profile = AuthCenterProfile('center', TENANT, 'https://auth.example.com', self.key_file)
        self.binding = AuthCenterBinding('center', TENANT, '123', 'meta', 'meta', 'oauth-user-1')
        self.calls = []
        self.status = 200
        self.headers = {'Set-Cookie': 'must-not-send=secret-cookie'}
        self.payload = {'success': True, 'data': {
            'channel': 'meta', 'agent_id': 'oauth-user-1', 'auth_status': 'active',
            'access_token': PLATFORM_TOKEN, 'token_type': 'Bearer',
            'expires_at': datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat(),
            'scopes': ['ads_read'],
        }}
        self.delay = 0
        self.error = None
        self.started = asyncio.Event()
        self.release = None
        self.raw = None
        async def center(request):
            self.calls.append(request)
            self.started.set()
            if self.release:
                await self.release.wait()
            await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            if self.raw is not None:
                return httpx.Response(self.status, content=self.raw, headers={'Content-Type': 'application/json', **self.headers})
            return httpx.Response(self.status, json=self.payload, headers=self.headers)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(center),
            headers={'Authorization': 'never-forward-default'}, cookies={'inherited': 'never-forward-cookie'})
        self.clock = [time.monotonic(), time.time()]
        self.cache = LeaseCache(monotonic=lambda: self.clock[0], wall_clock=lambda: self.clock[1])
        self.provider = AuthCenterProvider([self.profile], http=self.http, cache=self.cache)

    async def asyncTearDown(self):
        await self.provider.close()
        await self.http.aclose()
        self.temp.cleanup()

    async def get(self, binding=None):
        return await self.provider.resolve(binding or self.binding, 'account-ref')

    def advance(self, seconds):
        self.clock[0] += seconds
        self.clock[1] += seconds

    async def test_exact_identity_endpoint_no_fallback_and_only_service_key(self):
        lease = await self.get()
        self.assertEqual(lease.value, PLATFORM_TOKEN)
        self.assertIsNone(lease.token_version)  # Current API does not promise it.
        self.assertNotIn(PLATFORM_TOKEN, repr(lease))
        self.assertNotIn(API_KEY, repr(lease))
        self.assertEqual(len(self.calls), 1)
        req = self.calls[0]
        self.assertEqual(str(req.url), 'https://auth.example.com/api/v1/openapi/tokens/meta:oauth-user-1')
        self.assertEqual(req.headers['x-api-key'], API_KEY)
        self.assertNotIn('authorization', req.headers)
        self.assertNotIn('cookie', req.headers)
        self.assertNotIn(API_KEY, str(req.url))
        self.assertNotIn(PLATFORM_TOKEN, str(req.url))

    async def test_50_concurrent_fetches_use_singleflight(self):
        self.delay = 0.02
        leases = await asyncio.gather(*(self.get() for _ in range(50)))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(lease.value == PLATFORM_TOKEN for lease in leases))
        self.assertEqual(self.cache.flights, {})

    async def test_two_accounts_share_identity_lease_but_not_authorization(self):
        await self.get()
        await self.get(replace(self.binding, account_id='456'))
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(GatewayError):
            self.binding.require_scope(TENANT, 'meta', '456')

    async def test_cache_expires_and_observes_rotation(self):
        await self.get()
        self.payload['data']['access_token'] = 'SECRET_CANARY_ROTATED'
        self.advance(16)
        lease = await self.get()
        self.assertEqual(lease.value, 'SECRET_CANARY_ROTATED')
        self.assertEqual(len(self.calls), 2)

    async def test_expiry_buffer_not_extended_by_cache(self):
        self.payload['data']['expires_at'] = datetime.fromtimestamp(self.clock[1] + 35, timezone.utc).isoformat()
        await self.get()
        self.advance(6)
        with self.assertRaises(GatewayError) as caught:
            await self.get()
        self.assertEqual(caught.exception.code, 'AUTH_REQUIRED')
        self.assertEqual(len(self.calls), 2)

    async def test_absent_expiry_rejected_but_explicit_null_bounded(self):
        del self.payload['data']['expires_at']
        with self.assertRaises(GatewayError):
            await self.get()
        self.advance(2)
        self.payload['data']['expires_at'] = None
        self.assertIsNone((await self.get()).expires_at)
        self.advance(16)
        await self.get()
        self.assertEqual(len(self.calls), 3)

    async def test_failed_refresh_never_serves_stale(self):
        await self.get()
        self.advance(16)
        self.status = 503
        with self.assertRaises(GatewayError):
            await self.get()
        self.assertEqual(len(self.calls), 2)

    async def test_negative_cache_prevents_error_stampede_without_raw_body(self):
        self.status = 403
        self.payload = {'error': API_KEY + PLATFORM_TOKEN}
        results = await asyncio.gather(*(self.get() for _ in range(50)), return_exceptions=True)
        self.assertEqual(len(self.calls), 1)
        for error in results:
            self.assertIsInstance(error, GatewayError)
            self.assertEqual(error.code, 'AUTH_CENTER_DENIED')
            self.assertNotIn(API_KEY, str(error))
            self.assertNotIn(PLATFORM_TOKEN, str(error))
        self.assertNotIn(API_KEY, repr(self.cache.entries))
        self.assertNotIn(PLATFORM_TOKEN, repr(self.cache.entries))

    async def test_status_and_malformed_body_matrix(self):
        for status in (301, 302, 307, 401, 402, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                self.advance(2)
                self.status = status
                self.headers['Location'] = 'https://evil.example/' + API_KEY
                with self.assertRaises(GatewayError):
                    await self.get()
        self.assertEqual(len(self.calls), 10)
        self.assertTrue(all(req.url.host == 'auth.example.com' for req in self.calls))

    async def test_identity_platform_status_and_expiry_matrix(self):
        changes = [('channel', 'tiktok'), ('channel', 'facebook'), ('agent_id', 'other'),
                   ('auth_status', 'revoked'), ('auth_status', 'expired'), ('access_token', 123),
                   ('access_token', 'header\rinjection'), ('expires_at', '2026-01-01T00:00:00'),
                   ('expires_at', 10), ('token_version', True), ('token_version', 0)]
        base = dict(self.payload['data'])
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.advance(2)
                self.payload['data'] = {**base, field: value}
                with self.assertRaises(GatewayError):
                    await self.get()

    async def test_facebook_alias_is_explicit_not_automatic(self):
        self.payload['data']['channel'] = 'facebook'
        lease = await self.get(replace(self.binding, channel='facebook'))
        self.assertEqual(lease.platform, 'meta')
        self.assertTrue(self.calls[0].url.path.endswith('facebook:oauth-user-1'))

    async def test_tiktok_exact_identity(self):
        binding = replace(self.binding, platform='tiktok', channel='tiktok')
        self.payload['data']['channel'] = 'tiktok'
        lease = await self.get(binding)
        self.assertEqual(lease.platform, 'tiktok')
        self.assertTrue(self.calls[0].url.path.endswith('tiktok:oauth-user-1'))

    async def test_optional_version_is_preserved(self):
        self.payload['data']['token_version'] = 3
        self.assertEqual((await self.get()).token_version, 3)

    async def test_api_key_rotation_and_missing_file_bypass_cache(self):
        await self.get()
        rotated = API_KEY + 'ROTATED'
        self.key_file.write_text(rotated)
        await self.get()
        self.assertEqual(self.calls[-1].headers['x-api-key'], rotated)
        self.assertEqual(len(self.calls), 2)
        self.key_file.unlink()
        with self.assertRaises(GatewayError):
            await self.get()
        self.assertEqual(len(self.calls), 2)

    async def test_wrong_tenant_or_profile_has_zero_http_calls(self):
        for binding in (replace(self.binding, tenant_id=OTHER), replace(self.binding, profile='missing')):
            with self.assertRaises(GatewayError):
                await self.get(binding)
        self.assertEqual(self.calls, [])

    async def test_private_key_permission_rechecked_on_cache_hit(self):
        if os.name == 'nt':
            self.skipTest('POSIX permission test; Windows ACL acceptance is separate')
        await self.get()
        self.key_file.chmod(0o644)
        with self.assertRaises(GatewayError):
            await self.get()
        self.assertEqual(len(self.calls), 1)

    async def test_one_cancelled_waiter_does_not_cancel_shared_fetch(self):
        self.release = asyncio.Event()
        a = asyncio.create_task(self.get())
        await self.started.wait()
        b = asyncio.create_task(self.get())
        await asyncio.sleep(0)
        a.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await a
        self.release.set()
        self.assertEqual((await b).value, PLATFORM_TOKEN)
        self.assertEqual(len(self.calls), 1)

    async def test_invalidation_removes_cached_lease(self):
        lease = await self.get()
        self.provider.invalidate(lease)
        self.payload['data']['access_token'] = 'SECRET_CANARY_NEW'
        self.assertEqual((await self.get()).value, 'SECRET_CANARY_NEW')
        self.assertEqual(len(self.calls), 2)

    async def test_invalidation_during_fetch_cannot_repopulate(self):
        self.release = asyncio.Event()
        waiting = asyncio.create_task(self.get())
        await self.started.wait()
        cache_key = next(iter(self.cache.flights))
        self.cache.invalidate(cache_key)
        self.release.set()
        with self.assertRaises(GatewayError):
            await waiting
        self.assertEqual(len(self.cache.entries), 0)

    async def test_timeout_is_bounded(self):
        self.provider.profiles['center'] = replace(self.profile, timeout=0.1)
        self.delay = 0.2
        with self.assertRaises(GatewayError):
            await self.get()
        self.assertEqual(self.cache.flights, {})

    async def test_content_size_encoding_duplicate_keys_rejected(self):
        for raw, headers in ((b'x' * 131073, {}), (b'{"success":true,"success":false}', {}),
                             (b'{}', {'Content-Encoding': 'gzip'}), (b'{}', {'Content-Type': 'text/html'})):
            with self.subTest(headers=headers, length=len(raw)):
                self.advance(2)
                self.raw, self.headers = raw, headers
                with self.assertRaises(GatewayError):
                    await self.get()

    async def test_response_cookie_not_reused(self):
        await self.get()
        self.advance(16)
        await self.get()
        self.assertNotIn('cookie', self.calls[1].headers)

    async def test_fetch_capacity_and_lru_are_bounded(self):
        self.cache.max_fetches = 1
        self.release = asyncio.Event()
        first = asyncio.create_task(self.get())
        await self.started.wait()
        with self.assertRaises(GatewayError) as caught:
            await self.get(replace(self.binding, oauth_agent_id='different'))
        self.assertEqual(caught.exception.code, 'AUTH_CENTER_BUSY')
        self.release.set()
        await first
        self.cache.capacity = 1
        self.key_file.write_text(API_KEY + 'ROTATED')
        await self.get()
        self.assertEqual(len(self.cache.entries), 1)

    async def test_zero_ttl_does_not_reuse_finished_success(self):
        self.provider.profiles['center'] = replace(self.profile, cache_ttl=0)
        await self.get()
        await self.get()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.cache.entries), 0)

    async def test_close_cancels_fetches_and_rejects_new_work(self):
        self.release = asyncio.Event()
        task = asyncio.create_task(self.get())
        await self.started.wait()
        await self.provider.close()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(GatewayError):
            await self.get()


class ConfigurationTests(unittest.TestCase):
    def test_untrusted_origins_and_parameters_rejected(self):
        for origin in ('http://auth.example', 'https://u:p@auth.example', 'https://auth.example/path',
                       'https://auth.example?query', 'https://auth.example#frag', 'https://auth.example:80',
                       'https://auth.example\\evil', 'https://auth.example%2fevil'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                AuthCenterProfile('center', TENANT, origin, Path('/secret'))
        for field, value in (('cache_ttl', 61), ('timeout', 0), ('expiry_buffer', 121), ('cache_ttl', float('nan'))):
            with self.assertRaises(ValueError):
                AuthCenterProfile('center', TENANT, 'https://auth.example', Path('/secret'), **{field: value})

    def test_binding_validation_and_scope(self):
        binding = AuthCenterBinding('center', TENANT, '123', 'meta', 'facebook', 'oauth-1')
        for field, value in (('oauth_agent_id', '../evil'), ('channel', 'tiktok'), ('tenant_id', 'not-uuid'),
                             ('account_id', 'act_123'), ('profile', '../config')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(binding, **{field: value})
        for scope in ((OTHER, 'meta', '123'), (TENANT, 'tiktok', '123'), (TENANT, 'meta', '456')):
            with self.assertRaises(GatewayError):
                binding.require_scope(*scope)

    def test_protected_config_and_key_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / 'key'; key.write_text(API_KEY); key.chmod(0o600)
            config = root / 'profiles.json'
            item = {'name': 'center', 'tenant_id': TENANT, 'origin': 'https://auth.example.com', 'api_key_file': str(key)}
            config.write_text(json.dumps({'profiles': [item]})); config.chmod(0o600)
            self.assertEqual(load_profiles(config)[0].read_key(), API_KEY)
            config.write_text(json.dumps({'profiles': [item, item]}))
            with self.assertRaises(ValueError):
                load_profiles(config)
            if os.name != 'nt':
                link = root / 'link'; link.symlink_to(key)
                with self.assertRaises((OSError, ValueError)):
                    private_bytes(link, 4096)

    def test_old_schema_migration_preserves_direct_credentials_and_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'state'
            state = GatewayState(root, create=True)
            state.put_credential('direct', 'meta', PLATFORM_TOKEN)
            state.begin_write('owner', 'key', 'fingerprint')
            state.finish_write('owner', 'key', {'id': 'result'})
            with state.connection() as db:
                for column in ('provider', 'binding', 'revision'):
                    db.execute('ALTER TABLE credentials DROP COLUMN ' + column)
            with self.assertRaises(ValueError):
                GatewayState(root)
            migrated = GatewayState(root, create=True)
            self.assertEqual(migrated.resolve('direct', 'meta').value, PLATFORM_TOKEN)
            self.assertEqual(migrated.lookup_write('owner', 'key', 'fingerprint'), {'id': 'result'})

    def test_trusted_admin_binding_never_stores_platform_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'state'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(admin_main(['--state-dir', str(root), 'init']), 0)
                self.assertEqual(admin_main(['--state-dir', str(root), 'bind-auth-center', '--ref', 'remote',
                    '--profile', 'center', '--tenant', TENANT, '--account', '123', '--platform', 'meta',
                    '--channel', 'meta', '--oauth-agent-id', 'oauth-user-1', '--verified-binding']), 0)
            state = GatewayState(root)
            self.assertEqual(state.describe('remote', 'meta').provider, 'auth_center')
            with self.assertRaises(GatewayError):
                state.resolve('remote', 'meta')
            with state.connection() as db:
                self.assertEqual(db.execute("SELECT ciphertext FROM credentials WHERE ref='remote'").fetchone()[0], b'')
            self.assertNotIn(API_KEY, json.dumps(state.safe_status()))

    def test_external_binding_cannot_be_granted_to_another_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            state = GatewayState(Path(directory) / 'state', create=True)
            binding = AuthCenterBinding('center', TENANT, '123', 'meta', 'meta', 'oauth-user-1')
            state.put_auth_center_binding('remote', binding)
            with self.assertRaises(GatewayError):
                state.grant(subject='a', client='cli', workspace=OTHER, platform='meta', account='123', reference='remote')
            with self.assertRaises(GatewayError):
                state.grant(subject='a', client='cli', workspace=TENANT, platform='meta', account='456', reference='remote')

# Reuse fixture construction without inheriting/re-running its entire test class.
from gateway_tests import test_gateway as gateway_base


class AuthCenterPipelineTests(unittest.IsolatedAsyncioTestCase):
    body = gateway_base.GatewayTests.body
    token = gateway_base.GatewayTests.token
    post = gateway_base.GatewayTests.post

    def claims(self):
        data = gateway_base.GatewayTests.claims(self)
        data['workspace_id'] = TENANT
        return data

    async def asyncSetUp(self):
        await gateway_base.GatewayTests.asyncSetUp(self)
        self.key_file = self.root / 'center.key'
        self.key_file.write_text(API_KEY); self.key_file.chmod(0o600)
        self.center_calls = []
        self.center_status = 200
        self.center_started = asyncio.Event()
        self.center_release = None
        self.center_token = PLATFORM_TOKEN
        async def center(request):
            self.center_calls.append(request)
            self.center_started.set()
            if self.center_release:
                await self.center_release.wait()
            return httpx.Response(self.center_status, json={'success': True, 'data': {
                'channel': 'meta', 'agent_id': 'oauth-user-1', 'auth_status': 'active',
                'access_token': self.center_token, 'expires_at': None}})
        self.center_http = httpx.AsyncClient(transport=httpx.MockTransport(center), trust_env=False)
        self.profile = AuthCenterProfile('center', TENANT, 'https://auth.example.com', self.key_file)
        self.provider = AuthCenterProvider([self.profile], http=self.center_http)
        self.binding = AuthCenterBinding('center', TENANT, '123', 'meta', 'meta', 'oauth-user-1')
        self.state.put_auth_center_binding('meta-main', self.binding)
        self.state.grant(subject='agent-a', client='motata-cli', workspace=TENANT,
                         platform='meta', account='123', reference='meta-main')
        self.service.credentials = CredentialResolver(self.state, self.provider)

    async def asyncTearDown(self):
        await self.service.close()
        await self.center_http.aclose()
        await gateway_base.GatewayTests.asyncTearDown(self)

    def create_body(self, key='write-key'):
        return self.body(method='POST', body={'name': 'test', 'objective': 'OUTCOME_TRAFFIC', 'status': 'PAUSED'},
                         query={}, body_encoding='form', idempotency_key=key)

    async def test_full_pipeline_injects_only_platform_token_and_caches(self):
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.seen[0].headers['authorization'], 'Bearer ' + PLATFORM_TOKEN)
        self.assertNotIn(API_KEY, str(self.seen[0].headers))
        self.assertNotIn(self.token(), str(self.center_calls[0].headers))
        self.assertNotIn(API_KEY, response.text)
        self.assertNotIn(PLATFORM_TOKEN, response.text)
        await self.post()
        self.assertEqual(len(self.center_calls), 1)
        with self.state.connection() as db:
            self.assertEqual(db.execute("SELECT ciphertext FROM credentials WHERE ref='meta-main'").fetchone()[0], b'')

    async def test_50_pipeline_calls_use_one_remote_credential_fetch(self):
        results = await asyncio.gather(*(self.post() for _ in range(50)))
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertEqual(len(self.center_calls), 1)
        self.assertEqual(len(self.seen), 50)
        self.assertLessEqual(self.upstream_peak, 2)
        self.assertEqual(self.service.scheduler.active, 0)
        self.assertEqual(self.service.scheduler.admitted, 0)

    async def test_invalid_jwt_scope_and_workspace_have_zero_provider_requests(self):
        self.assertEqual((await self.post(token='invalid')).status_code, 401)
        for field, value in (('scope', 'gateway:use'), ('workspace_id', OTHER), ('sub', 'other')):
            claims = self.claims(); claims[field] = value
            self.assertEqual((await self.post(token=self.token(claims=claims))).status_code, 403)
        self.assertEqual(len(self.center_calls), 0)
        self.assertEqual(len(self.seen), 0)
        self.assertEqual(self.state.resolve_count, 0)

    async def test_local_grant_revocation_denies_even_with_warm_cache(self):
        await self.post()
        self.state.remove_grant(subject='agent-a', client='motata-cli', workspace=TENANT, platform='meta', account='123')
        self.assertEqual((await self.post()).status_code, 403)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(len(self.center_calls), 1)

    async def test_session_revoked_during_fetch_sends_zero_platform_requests(self):
        self.center_release = asyncio.Event()
        waiting = asyncio.create_task(self.post())
        await self.center_started.wait()
        self.state.revoke('sid', 'session-a')
        self.center_release.set()
        self.assertEqual((await waiting).status_code, 401)
        self.assertEqual(len(self.seen), 0)

    async def test_binding_replaced_during_fetch_sends_zero_platform_requests(self):
        self.center_release = asyncio.Event()
        waiting = asyncio.create_task(self.post())
        await self.center_started.wait()
        self.state.put_auth_center_binding('meta-main', replace(self.binding, oauth_agent_id='new-identity'))
        self.center_release.set()
        result = await waiting
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['error']['code'], 'CREDENTIAL_CHANGED')
        self.assertEqual(len(self.seen), 0)

    async def test_binding_disabled_during_fetch_sends_zero_platform_requests(self):
        self.center_release = asyncio.Event()
        waiting = asyncio.create_task(self.post())
        await self.center_started.wait()
        self.state.disable_credential('meta-main')
        self.center_release.set()
        self.assertEqual((await waiting).status_code, 503)
        self.assertEqual(len(self.seen), 0)

    async def test_failed_credential_fetch_does_not_poison_write_receipt(self):
        self.center_status = 403
        response = await self.post(self.create_body())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['write_outcome'], 'not_sent')
        with self.state.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)
        self.assertEqual(len(self.seen), 0)
        self.center_status = 200
        self.provider.cache.entries.clear()
        self.reply = {'id': '999'}
        response = await self.post(self.create_body())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.seen), 1)

    async def test_completed_receipt_replay_does_not_refetch_credentials(self):
        self.reply = {'id': '999'}
        self.assertEqual((await self.post(self.create_body())).status_code, 200)
        self.key_file.unlink()  # safe saved result needs authorization, not a new secret
        response = await self.post(self.create_body())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['replayed'])
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(len(self.center_calls), 1)

    async def test_expired_jwt_during_provider_await_sends_zero_requests(self):
        self.center_release = asyncio.Event()
        waiting = asyncio.create_task(self.post())
        await self.center_started.wait()
        with patch('motata_gateway.service.time.time', return_value=time.time() + 700):
            self.center_release.set()
            response = await waiting
        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.seen), 0)

    async def test_upstream_rejection_invalidates_cache_without_write_replay(self):
        self.reply = {'error': {'code': 190, 'message': 'token invalid'}}
        self.http_status = 400
        response = await self.post(self.create_body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(len(self.provider.cache.entries), 0)
        self.reply = {'data': []}; self.http_status = 200
        self.center_token = 'SECRET_CANARY_ROTATED_PLATFORM'
        await self.post()
        self.assertEqual(len(self.center_calls), 2)
        self.assertEqual(self.seen[-1].headers['authorization'], 'Bearer ' + self.center_token)

    async def test_service_key_reflected_in_business_data_is_blocked(self):
        self.reply = {'data': [{'id': '456', 'name': API_KEY}]}
        response = await self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(API_KEY, response.text)

    async def test_missing_provider_does_not_fall_back_to_old_direct_secret(self):
        self.service.credentials = CredentialResolver(self.state)
        response = await self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'AUTH_CENTER_NOT_CONFIGURED')
        self.assertEqual(len(self.center_calls), 0)
        self.assertEqual(len(self.seen), 0)
        await self.provider.close()
