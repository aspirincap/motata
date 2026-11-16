from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from motata_cli.auth_center.provider import AuthCenterCredential, AuthCenterError, AuthCenterProvider, resolve_tiktok_credential
from motata_cli.auth_center.usage import UsageStore
from motata_cli.tiktok.client import TikTokClient
from motata_cli.transport.tiktok_http import TikTokHTTP


def reply(data=None, status=200, *, platform=False):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"code": 0, "data": data or {}} if platform else {"success": True, "data": data or {}}).encode()
    response._content_consumed = True
    return response


class AuthCenterProviderTests(unittest.TestCase):
    def setUp(self):
        limiter = patch('motata_cli.transport.tiktok_http.default_read_throttle')
        self.limiter = limiter.start().return_value
        self.limiter.acquire_delay.return_value = 0.0
        self.addCleanup(limiter.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.session = Mock()
        self.store = UsageStore(Path(self.directory.name) / "usage.db")
        self.provider = AuthCenterProvider("fixture-api-key", session=self.session, usage_store=self.store)

    def stub(self, method, url, **kwargs):
        if url.endswith("/billing/token-usage"):
            return reply({"allowed": True, "blocked": False})
        if url.endswith("/token"):
            account = url.split("/")[-2]
            return reply({"account_id": account, "channel": "tiktok", "agent": {"agent_id": "agent-" + account},
                          "token": {"access_token": "platform-" + account, "expires_at": None}})
        if "/ad-accounts/" in url:
            return reply({"account_id": url.split("/")[-1], "channel": "tiktok"})
        raise AssertionError(url)

    def test_account_metadata_null_expiry_and_separate_credentials(self):
        self.session.request.side_effect = self.stub
        lease = self.provider.acquire("123")
        self.assertEqual((lease.account_id, lease.agent_id, lease.expires_at), ("123", "agent-123", None))
        self.assertNotIn("platform-123", repr(lease))
        platform = Mock()
        platform.request.return_value = reply({"list": []}, platform=True)
        client = TikTokClient(AuthCenterCredential(self.provider), transport=TikTokHTTP(AuthCenterCredential(self.provider), session=platform))
        client.list_campaigns("123")
        self.assertEqual(platform.request.call_args.kwargs["headers"]["Access-Token"], "platform-123")
        self.assertNotIn("X-API-Key", platform.request.call_args.kwargs["headers"])
        self.assertEqual(self.session.request.call_args.kwargs["headers"]["X-API-Key"], "fixture-api-key")
        self.assertNotIn("Access-Token", self.session.request.call_args.kwargs["headers"])
        with self.store.connect() as db:
            state, payload = db.execute("SELECT state, payload FROM usage_events").fetchone()
        self.assertEqual(state, "acknowledged")
        self.assertNotIn("fixture-api-key", payload)
        self.assertNotIn("platform-123", payload)

    def test_invalid_keys_quota_and_malformed_contract_fail_before_platform(self):
        for status in (401, 402, 403, 404, 429, 500):
            self.session.request.return_value = reply(status=status)
            with self.assertRaises(AuthCenterError) as caught:
                self.provider.acquire("123")
            self.assertEqual(caught.exception.http_status, status)
        self.session.request.return_value = reply({"account_id": "other", "channel": "tiktok"})
        with self.assertRaisesRegex(AuthCenterError, "mismatch"):
            self.provider.acquire("123")

    def test_multi_account_leases_and_bearer_header(self):
        self.provider.header_mode = "bearer"
        self.session.request.side_effect = self.stub
        credential = AuthCenterCredential(self.provider)
        a = credential.for_request({"advertiser_id": "123"})
        b = credential.for_request({"advertiser_id": "456"})
        self.assertNotEqual(a.get_token(), b.get_token())
        self.assertIs(a, credential.for_request({"advertiser_id": "123"}))
        self.assertEqual(self.session.request.call_args.kwargs["headers"]["Authorization"], "Bearer fixture-api-key")
        self.assertNotIn("X-API-Key", self.session.request.call_args.kwargs["headers"])

    def test_unknown_usage_does_not_replay_or_mask_completed_request(self):
        def handler(method, url, **kwargs):
            if method == "POST":
                raise requests.ReadTimeout("private response")
            return self.stub(method, url, **kwargs)
        self.session.request.side_effect = handler
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        platform.request.return_value = reply({"list": [{"campaign_id": "1"}]}, platform=True)
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        with redirect_stderr(io.StringIO()) as stderr:
            result = client.list_campaigns("123")
        self.assertEqual(result["data"]["list"][0]["campaign_id"], "1")
        self.assertIn("unknown", stderr.getvalue())
        with self.assertRaises(Exception):
            client.list_campaigns("123")
        platform.request.assert_called_once()
        self.assertEqual(sum(call.args[0] == "POST" for call in self.session.request.call_args_list), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM usage_events").fetchone()[0], "unknown")

    @patch('motata_cli.transport.tiktok_http.time.sleep')
    def test_rate_limit_retry_keeps_credential_and_accounts_each_attempt(self, sleep):
        self.session.request.side_effect = self.stub
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        limited = reply(platform=True)
        limited._content = json.dumps({'code': 40100, 'message': 'Too many requests'}).encode()
        platform.request.side_effect = [limited, reply({'list': []}, platform=True)]
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        client.list_campaigns('123')
        self.assertEqual(platform.request.call_count, 2)
        self.assertEqual(credential.leases['123'].token, 'platform-123')
        self.assertEqual(sum(c.args[1].endswith('/token') for c in self.session.request.call_args_list), 1)
        usage = [c.kwargs['json']['request_id'] for c in self.session.request.call_args_list if c.args[1].endswith('/billing/token-usage')]
        self.assertEqual(len(set(usage)), 2)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM usage_events WHERE state='acknowledged'").fetchone()[0], 2)

    @patch('motata_cli.transport.tiktok_http.time.sleep')
    def test_qps_limit_keeps_lease_and_records_each_attempt(self, sleep):
        self.session.request.side_effect = self.stub
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        limited = reply(platform=True)
        limited._content = json.dumps({'code': 40002, 'message': 'OVER_QPS_LIMIT'}).encode()
        platform.request.side_effect = [limited, reply(platform=True)]
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        client.list_campaigns('123')
        self.assertEqual(platform.request.call_count, 2)
        self.assertEqual(credential.leases['123'].token, 'platform-123')
        self.assertEqual(sum(c.args[1].endswith('/token') for c in self.session.request.call_args_list), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM usage_events WHERE state='acknowledged'").fetchone()[0], 2)
        self.limiter.defer.assert_called_once()

    @patch('motata_cli.transport.tiktok_http.time.sleep')
    def test_shared_wait_refreshes_lease_before_sending_and_billing(self, sleep):
        from motata_cli.tiktok.endpoints import ENDPOINTS
        from motata_cli.tiktok.throttle import ReadThrottle
        clock = [1000.0]
        limiter = ReadThrottle(Path(self.directory.name) / 'limits.db', clock=lambda: clock[0])
        limiter.defer(ENDPOINTS['campaign_get'], 65)
        token_count = [0]
        def handler(method, url, **kwargs):
            if url.endswith('/token'):
                token_count[0] += 1
                return reply({'account_id': '123', 'channel': 'tiktok', 'agent': {'agent_id': 'agent-123'},
                              'token': {'access_token': f'platform-token-{token_count[0]}', 'expires_at': None}})
            return self.stub(method, url, **kwargs)
        self.session.request.side_effect = handler
        credential = AuthCenterCredential(self.provider)
        def wait(delay):
            clock[0] += delay
            credential.leases['123'].issued_at -= delay
            with self.store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM usage_events').fetchone()[0], 0)
        sleep.side_effect = wait
        platform = Mock()
        platform.request.return_value = reply(platform=True)
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform, read_throttle=limiter))
        client.list_campaigns('123')
        self.assertEqual(token_count[0], 2)
        self.assertEqual(platform.request.call_args.kwargs['headers']['Access-Token'], 'platform-token-2')
        platform.request.assert_called_once()
        self.assertEqual(sum(c.args[1].endswith('/billing/token-usage') for c in self.session.request.call_args_list), 1)

    def test_shared_wait_budget_stops_before_platform_usage_is_prepared(self):
        self.session.request.side_effect = self.stub
        self.limiter.acquire_delay.return_value = 181
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        with self.assertRaises(Exception) as caught:
            client.list_campaigns('123')
        self.assertEqual((caught.exception.attempts, caught.exception.retry_stop_reason), (0, 'wait_budget'))
        platform.request.assert_not_called()
        self.assertFalse(any(c.args[1].endswith('/billing/token-usage') for c in self.session.request.call_args_list))
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM usage_events').fetchone()[0], 0)

    @patch('motata_cli.transport.tiktok_http.time.sleep')
    def test_true_auth_error_invalidates_lease_without_retry(self, sleep):
        self.session.request.side_effect = self.stub
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        rejected = reply(platform=True)
        rejected._content = json.dumps({'code': 40105, 'message': 'Invalid access token'}).encode()
        platform.request.return_value = rejected
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        with self.assertRaises(Exception) as caught:
            client.list_campaigns('123')
        self.assertEqual(caught.exception.category, 'auth')
        self.assertEqual(credential.leases['123'].token, '')
        platform.request.assert_called_once()
        sleep.assert_not_called()

    @patch('motata_cli.transport.tiktok_http.time.sleep')
    def test_usage_failure_during_rate_limit_stops_the_next_platform_attempt(self, sleep):
        from motata_cli.common.output import request_with_retry
        def handler(method, url, **kwargs):
            if url.endswith('/billing/token-usage'):
                raise requests.ReadTimeout('billing timeout')
            return self.stub(method, url, **kwargs)
        self.session.request.side_effect = handler
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        limited = reply(platform=True)
        limited._content = json.dumps({'code': 40100}).encode()
        platform.request.return_value = limited
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        with redirect_stderr(io.StringIO()), self.assertRaisesRegex(Exception, 'acknowledgement is unknown'):
            request_with_retry(client.list_campaigns, '123')
        platform.request.assert_called_once()
        self.assertEqual(sum(c.args[1].endswith('/billing/token-usage') for c in self.session.request.call_args_list), 1)

    @patch('motata_cli.transport.tiktok_http.time.sleep')
    def test_read_retry_refreshes_naturally_expired_lease(self, sleep):
        self.session.request.side_effect = self.stub
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        limited = reply(platform=True)
        limited._content = json.dumps({'code': 40100}).encode()
        platform.request.side_effect = [limited, reply({'list': []}, platform=True)]
        sleep.side_effect = lambda delay: setattr(credential.leases['123'], 'issued_at', credential.leases['123'].issued_at - 61)
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        client.list_campaigns('123')
        self.assertEqual(sum(c.args[1].endswith('/token') for c in self.session.request.call_args_list), 2)
        self.assertEqual(platform.request.call_count, 2)

    @patch('motata_cli.transport.tiktok_http.time.sleep')
    def test_write_proof_may_retry_but_write_itself_is_sent_once(self, sleep):
        from motata_cli.common.output import request_with_retry
        self.session.request.side_effect = self.stub
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        def limited():
            result = reply(platform=True)
            result._content = json.dumps({'code': 40100}).encode()
            return result
        platform.request.side_effect = [limited(), reply({'list': [{'advertiser_id': '123'}]}, platform=True), limited()]
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        with self.assertRaises(Exception) as caught:
            request_with_retry(client.create_campaign, {'advertiser_id': '123'})
        self.assertEqual(caught.exception.category, 'rate_limit')
        self.assertEqual([c.args[0] for c in platform.request.call_args_list], ['GET', 'GET', 'POST'])
        sleep.assert_called_once()

    def test_account_fallback_token_must_prove_access_before_write(self):
        self.session.request.side_effect = self.stub
        credential = AuthCenterCredential(self.provider)
        platform = Mock()
        platform.request.return_value = reply({"list": []}, platform=True)
        client = TikTokClient(credential, transport=TikTokHTTP(credential, session=platform))
        with self.assertRaisesRegex(Exception, "cannot access"):
            client.create_campaign({"advertiser_id": "123"})
        self.assertEqual(platform.request.call_args.args[0], "GET")
        platform.request.assert_called_once()

    def test_source_selection_no_silent_environment_fallback(self):
        with patch("motata_cli.init_profile.load_init_profile", return_value={"token_source": "auth-center"}), \
                patch.dict(os.environ, {"TIKTOK_ACCESS_TOKEN": "wrong-token"}, clear=True):
            with self.assertRaisesRegex(Exception, "AUTH_CENTER_API_KEY"):
                resolve_tiktok_credential(account_id="123")
            token, source = resolve_tiktok_credential(access_token="explicit-token")
            self.assertEqual(token, "explicit-token")
            with self.assertRaisesRegex(Exception, "conflicts"):
                resolve_tiktok_credential(access_token="explicit-token", token_source="auth-center")

    def test_cli_to_auth_center_to_platform(self):
        from motata_cli.__main__ import main
        captured = []
        def http(session, method, url, **kwargs):
            captured.append((url, kwargs["headers"]))
            if url.startswith("https://business-api.tiktok.com/"):
                return reply({"list": [{"campaign_id": "campaign-1"}]}, platform=True)
            return self.stub(method, url, **kwargs)
        with patch.dict(os.environ, {"AUTH_CENTER_API_KEY": "fixture-api-key"}), \
                patch("requests.Session.request", autospec=True, side_effect=http), \
                patch("motata_cli.auth_center.usage.UsageStore", return_value=self.store), \
                redirect_stdout(io.StringIO()) as stdout:
            result = main(["tiktok", "campaigns", "list", "--advertiser-id", "123", "--token-source", "auth-center"])
        self.assertEqual(result, 0)
        self.assertIn("campaign-1", stdout.getvalue())
        self.assertNotIn("platform-123", stdout.getvalue())
        self.assertTrue(any("business-api.tiktok.com" in url for url, _ in captured))


if __name__ == "__main__":
    unittest.main()
