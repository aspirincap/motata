from __future__ import annotations

import io
import json
import importlib.util
from pathlib import Path
import sys
import types
import traceback
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

import requests

from motata_cli.common.security import redact
from motata_cli.auth_center import fetch_token
from motata_cli.meta.client import MetaClient, configure_meta_debug
# Isolate client tests from unrelated CLI command registration and circular imports.
_spec = importlib.util.spec_from_file_location(
    "_network_test_tiktok.client",
    Path(__file__).resolve().parents[1] / "motata_cli/tiktok/client.py",
)
_module = importlib.util.module_from_spec(_spec)
_sdk = types.ModuleType("_network_test_tiktok.sdk")
_sdk.get_business_api_client = Mock(side_effect=AssertionError("SDK construction forbidden"))
with patch.dict(sys.modules, {"_network_test_tiktok": types.ModuleType("_network_test_tiktok"), "_network_test_tiktok.sdk": _sdk}):
    _spec.loader.exec_module(_module)
TikTokClient = _module.TikTokClient


class NetworkSecurityTests(unittest.TestCase):
    def setUp(self):
        # Fail closed: even an accidentally unmocked request cannot reach a socket.
        self.socket_guard = patch("socket.socket.connect", side_effect=AssertionError("Real network forbidden"))
        self.socket_guard.start()
        self.addCleanup(self.socket_guard.stop)
        self.addCleanup(configure_meta_debug, False)

    def response(self, payload):
        response = Mock()
        response.json.return_value = payload
        return response

    def test_auth_context_repr_does_not_expose_token(self):
        from motata_cli.common.auth import resolve_auth
        auth = resolve_auth(account_id='123', access_token='fake-repr-secret')
        self.assertNotIn('fake-repr-secret', repr(auth))
        self.assertEqual(auth.access_token, 'fake-repr-secret')

    def test_recursive_redaction_does_not_mutate(self):
        payload = {"Authorization": "Bearer hidden", "nested": [{"X-API-Key": "key"}],
                   "url": "https://user:pass@host/?access_token=urlsecret&limit=2",
                   "error": 'refresh_token="other" access%5Ftoken=encoded'}
        safe = str(redact(payload))
        for secret in ("hidden", "urlsecret", "other", "encoded", "user:pass"):
            self.assertNotIn(secret, safe)
        self.assertEqual(payload["Authorization"], "Bearer hidden")
        self.assertIn("limit=2", safe)

    def test_serialized_nested_json_redacts_escaped_secret_values(self):
        secret = 'prefix"suffix'
        nested = json.dumps({"headers": {"Authorization": secret},
                             "payload": json.dumps({"client_secret": secret})})
        safe = redact(nested)
        self.assertNotIn("prefix", safe)
        self.assertNotIn("suffix", safe)
        self.assertIn("[REDACTED]", safe)

    def test_cookie_header_redacts_all_session_values(self):
        raw = "Cookie: foo=first-secret; session=second-secret\nSet-Cookie: session=third-secret; HttpOnly\nX-Trace: safe"
        for value in (raw, json.dumps({'message': raw}), {'message': raw}):
            safe = str(redact(value))
            for secret in ('first-secret', 'second-secret', 'third-secret'):
                self.assertNotIn(secret, safe)
            self.assertIn('X-Trace', safe)
        client = MetaClient('test-token', version='v1')
        with self.assertRaises(RuntimeError) as raised:
            client._handle(self.response({'error': {'message': raw, 'code': 100}}))
        self.assertNotIn('second-secret', str(raised.exception))

    def test_meta_malformed_error_is_safe(self):
        client = MetaClient("primary-secret", version="v1")
        with patch("motata_cli.meta.client.requests.post", return_value=self.response({"error": "body-secret"})) as request:
            with self.assertRaises(RuntimeError) as raised:
                client.post("items")
        self.assertNotIn("body-secret", str(raised.exception))
        self.assertIn("verify remote state", str(raised.exception))
        request.assert_called_once()

    def test_tiktok_nested_api_error_is_safe(self):
        client = TikTokClient.__new__(TikTokClient)
        client.access_token = "private-token"
        with self.assertRaises(Exception) as raised:
            client._check_response({"code": 400, "message": {
                "headers": {"Authorization": 'prefix"suffix'},
                "payload": [{"api_key": "nested-secret"}]}})
        for secret in ("prefix", "suffix", "nested-secret"):
            self.assertNotIn(secret, str(raised.exception))

    def test_meta_pagination_debug_redacts_url_and_nested_payload(self):
        client = MetaClient("primary-secret", version="v1")
        next_url = "https://graph.facebook.com/v1/items?access_token=page-secret&limit=1"
        stderr = io.StringIO()
        configure_meta_debug(True)
        with patch("motata_cli.meta.client.requests.get", side_effect=[
            self.response({"data": [{"id": "1"}], "paging": {"next": next_url}}),
            self.response({"data": [{"id": "2"}]})]) as get, redirect_stderr(stderr):
            rows = client.paginate("items", params={"nested": {"api_key": "nested-secret"}})
        self.assertEqual(len(rows), 2)
        for secret in ("primary-secret", "page-secret", "nested-secret"):
            self.assertNotIn(secret, stderr.getvalue())
        self.assertEqual(get.call_args.args[0], next_url)
        self.assertEqual(get.call_args.kwargs["timeout"], (10, 120))

    def test_meta_transport_errors_and_write_timeouts(self):
        client = MetaClient("secret-value", version="v1")
        for method in ("get", "post", "delete"):
            with self.subTest(method=method), patch("motata_cli.meta.client.requests." + method,
                    side_effect=requests.ReadTimeout("secret-value arbitrary-body-secret")) as request:
                try:
                    getattr(client, method)("items")
                except RuntimeError as exc:
                    text = "".join(traceback.format_exception(exc))
                    self.assertNotIn("arbitrary-body-secret", text)
                    self.assertNotIn("secret-value", text)
                    if method != "get":
                        self.assertIn("verify remote state", str(exc))
                else:
                    self.fail("Expected safe error")
                request.assert_called_once()
                self.assertEqual(request.call_args.kwargs["timeout"], (10, 120))

    def test_meta_api_error_redacts_message(self):
        client = MetaClient("secret-value", version="v1")
        with patch("motata_cli.meta.client.requests.get", return_value=self.response({"error": {
                "code": 190, "message": "secret-value access_token=other-secret"}})):
            with self.assertRaises(RuntimeError) as raised:
                client.get("items")
        self.assertNotIn("secret-value", str(raised.exception))
        self.assertNotIn("other-secret", str(raised.exception))
        self.assertIn("190", str(raised.exception))

    def test_tiktok_sdk_timeout_and_safe_exception(self):
        client = TikTokClient.__new__(TikTokClient)
        client.access_token = "private-token"
        fn = Mock(return_value={"code": 0})
        self.assertEqual(client._invoke(fn), {"code": 0})
        self.assertEqual(fn.call_args.kwargs["_request_timeout"], (10, 120))
        fn.side_effect = RuntimeError("private-token arbitrary-secret")
        with self.assertRaises(Exception) as raised:
            client._invoke(fn)
        self.assertNotIn("arbitrary-secret", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_tiktok_raw_read_write_and_invalid_json_no_retry(self):
        client = TikTokClient.__new__(TikTokClient)
        client.access_token = "private-token"
        for method in ("GET", "POST", "PUT", "DELETE"):
            with self.subTest(method=method), patch("requests.request",
                    side_effect=requests.ReadTimeout("private-token other-secret")) as request:
                with self.assertRaises(Exception) as raised:
                    client._raw_request(method, "campaign/create/", timeout=15)
                request.assert_called_once()
                self.assertEqual(request.call_args.kwargs["timeout"], (10, 15))
                self.assertNotIn("other-secret", str(raised.exception))
                self.assertEqual("verify remote state" in str(raised.exception), method != "GET")
        response = self.response({})
        response.json.side_effect = ValueError("private-response-body")
        with patch("requests.request", return_value=response):
            with self.assertRaises(Exception) as raised:
                client._raw_request("POST", "campaign/create/")
        self.assertNotIn("private-response-body", str(raised.exception))
        self.assertIn("verify remote state", str(raised.exception))

    def run_inventory(self, pages, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(fetch_token, "request_payload", side_effect=pages) as request, redirect_stdout(stdout), redirect_stderr(stderr):
            result = fetch_token.main(["--mode", "inventory", "--api-key", "api-secret", "--account-id", "target", *extra])
        return result, stdout.getvalue(), stderr.getvalue(), request

    def test_inventory_cross_page_lookup_even_after_short_page(self):
        pages = [{"data": {"accounts": [{"account_id": "other", "token": {"access_token": "other-secret"}}]}},
                 {"data": {"accounts": [{"account_id": "target", "token": {"access_token": "target-token"}}]}}]
        result, out, err, request = self.run_inventory(pages, "--channel", "meta")
        self.assertEqual((result, out, err), (0, "target-token\n", ""))
        self.assertIn("channel=meta", request.call_args.kwargs["query"])
        self.assertIn("page=2", request.call_args.kwargs["query"])

    def test_inventory_missing_and_repeated_page_do_not_dump_tokens(self):
        page = {"data": {"accounts": [{"account_id": "other", "token": {"access_token": "other-secret"}}]}}
        for pages in ([page, {"data": {"accounts": []}}], [page, page]):
            result, out, err, request = self.run_inventory(pages)
            self.assertEqual(result, 1)
            self.assertEqual(out, "")
            self.assertNotIn("other-secret", err)
            self.assertEqual(request.call_count, 2)

    def test_inventory_malformed_pages_and_missing_token_are_safe(self):
        for page in ({"data": "body-secret"}, {"data": {"accounts": ["body-secret"]}},
                     {"data": {"accounts": [{"account_id": "target", "token": "body-secret",
                                              "refresh_token": "refresh-secret"}]}}):
            result, out, err, request = self.run_inventory([page])
            self.assertEqual(result, 1)
            self.assertEqual(out, "")
            self.assertNotIn("body-secret", err)
            self.assertNotIn("refresh-secret", err)
            request.assert_called_once()

    def test_inventory_later_page_failure_is_safe(self):
        first = {"data": {"accounts": [{"account_id": "other"}]}}
        result, out, err, request = self.run_inventory([first, RuntimeError("api-secret body-secret")])
        self.assertEqual((result, out), (1, ""))
        self.assertNotIn("api-secret", err)
        self.assertNotIn("body-secret", err)
        self.assertEqual(request.call_count, 2)

    def test_auth_http_body_and_exception_not_echoed(self):
        error = urllib.error.HTTPError("https://host/?api_key=url-secret", 403, "body-secret", {}, io.BytesIO(b"response-secret"))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                fetch_token.request_payload(base_url="https://host", path="/tokens", api_key="api-secret", header_mode="bearer")
        self.assertIn("403", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        for secret in ("body-secret", "response-secret", "url-secret"):
            self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
