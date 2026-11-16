from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from motata_cli.tiktok.client import TikTokClient
from motata_cli.tiktok.codec import compact, query
from motata_cli.tiktok.endpoints import ENDPOINTS, endpoint_for
from motata_cli.tiktok.errors import TikTokError
from motata_cli.transport.tiktok_http import TikTokHTTP


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result._content_consumed = True
    return result


class CaptureTransport:
    def request(self, endpoint, *, params=None, body=None, files=None, timeout=None):
        self.actual = {"method": endpoint.method, "path": endpoint.path, "query": compact(params or {}),
                       "body": body if not files else None, "form": body if files else {},
                       "files": list(files or {})}
        return {"code": 0, "data": {}}

    def close(self):
        pass


class TikTokHttpTests(unittest.TestCase):
    def setUp(self):
        limiter = patch('motata_cli.transport.tiktok_http.default_read_throttle')
        limiter.start().return_value.acquire_delay.return_value = 0.0
        self.addCleanup(limiter.stop)

    def test_all_existing_http_operations_keep_their_contracts(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/tiktok_existing_http_contracts.json").read_text())
        self.assertEqual(len(fixtures), 31)
        transport = CaptureTransport()
        client = TikTokClient("fixture-token", transport=transport)
        for case in fixtures:
            with self.subTest(method=case["client_method"], smart=case["kwargs"].get("smart_plus")):
                getattr(client, case["client_method"])(**case["kwargs"])
                self.assertEqual(transport.actual, case["expected"])

    def test_all_51_operations_match_pre_migration_wire_contracts(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/tiktok_http_contracts.json").read_text())
        self.assertEqual(len(fixtures), 51)
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "sample.bin"
            file.write_bytes(b"motata-upload-fixture")
            transport = CaptureTransport()
            client = TikTokClient("fixture-token", transport=transport)
            for case in fixtures:
                with self.subTest(method=case["client_method"], smart=case["kwargs"].get("smart_plus")):
                    kwargs = {k: str(file) if v == "__FILE__" else v for k, v in case["kwargs"].items()}
                    expected = {**case['expected'], 'query': compact(case['expected']['query'])}
                    if case['client_method'] == 'update_adgroup' and kwargs.get('smart_plus'):
                        # Historical fixtures exercised serialization with an
                        # incomplete payload. The reviewed update contract now
                        # validates its required ID before reaching transport.
                        kwargs['payload'] = {**kwargs['payload'], 'adgroup_id': '456'}
                        expected['body'] = {**expected['body'], 'adgroup_id': '456'}
                    getattr(client, case["client_method"])(**kwargs)
                    # Omit optional None query arguments; preserve explicit JSON null bodies.
                    self.assertEqual(transport.actual, expected)

    def test_empty_and_unknown_fields_reach_wire(self):
        session = Mock()
        session.request.return_value = response({"code": 0, "data": {"new": 1}})
        client = TikTokClient("private-token", transport=TikTokHTTP("private-token", session=session))
        payload = {"advertiser_id": "123", "adgroup_id": "456", "adgroup_name": "renamed",
                   "future": {"zero": 0, "false": False, "null": None, "clear": []}}
        self.assertEqual(client.update_adgroup(payload, smart_plus=True)["data"], {"new": 1})
        self.assertEqual(session.request.call_args.kwargs["json"], payload)
        client.list_adgroups("123", filtering={"adgroup_ids": []}, fields=[])
        self.assertEqual(session.request.call_args.kwargs["params"]["fields"], "[]")
        self.assertEqual(query({"x": False, "y": 0, "z": None}), {"x": "false", "y": "0"})

    def test_smartplus_update_rejects_create_only_fields_without_network(self):
        session = Mock()
        client = TikTokClient('token', transport=TikTokHTTP('token', session=session))
        for field in ('identity_id', 'identity_type', 'native_series_id', 'drama_selling_setting'):
            with self.assertRaisesRegex(Exception, 'create-only'):
                client.update_adgroup({'advertiser_id': '123', 'adgroup_id': '456', field: 'new'}, smart_plus=True)
        session.request.assert_not_called()

    def test_errors_are_structured_and_never_retried(self):
        for status, code, category in ((200, 40108, "rate_limit"), (401, 40001, "auth"), (503, 500, "network")):
            session = Mock()
            session.request.return_value = response({"code": code, "message": "private-token", "request_id": "trace"}, status)
            transport = TikTokHTTP("private-token", session=session)
            with self.assertRaises(TikTokError) as caught:
                transport.request(ENDPOINTS["campaign_create"], body={})
            self.assertEqual(caught.exception.category, category)
            self.assertEqual(caught.exception.http_status, status)
            self.assertNotIn("private-token", str(caught.exception))
            session.request.assert_called_once()

    def test_write_timeout_and_invalid_success_are_unknown(self):
        for failure in (requests.ReadTimeout("private-token"), response({}), response([])):
            session = Mock()
            if isinstance(failure, Exception):
                session.request.side_effect = failure
            else:
                session.request.return_value = failure
            with self.assertRaises(TikTokError) as caught:
                TikTokHTTP("private-token", session=session).request(ENDPOINTS["campaign_create"], body={})
            self.assertEqual(caught.exception.write_outcome, "unknown")
            session.request.assert_called_once()

    def test_non_json_errors_preserve_http_category_and_ambiguous_write(self):
        for status, category in ((401, 'auth'), (429, 'rate_limit'), (503, 'network')):
            session = Mock()
            result = response({}, status)
            result._content = b'<html>upstream failure</html>'
            result.headers['Retry-After'] = '10'
            session.request.return_value = result
            with self.assertRaises(TikTokError) as caught:
                TikTokHTTP('private-token', session=session).request(ENDPOINTS['campaign_create'], body={})
            self.assertEqual(caught.exception.http_status, status)
            self.assertEqual(caught.exception.category, category)
            self.assertEqual(caught.exception.write_outcome, 'unknown')
            self.assertEqual(caught.exception.retry_after, '10')
            session.request.assert_called_once()

    def test_oauth_application_secret_echo_is_redacted(self):
        session = Mock()
        session.request.return_value = response({'code': 40001, 'message': 'rejected private-app-secret'})
        client = TikTokClient('token', transport=TikTokHTTP('token', session=session))
        with self.assertRaises(TikTokError) as caught:
            client.oauth2_advertiser_get('app', 'private-app-secret')
        self.assertNotIn('private-app-secret', str(caught.exception))
        self.assertIn('[REDACTED]', str(caught.exception))

    def test_origin_method_and_implicit_auth_boundaries(self):
        for path in ("https://other.test/campaign/get/", "//other.test/", "../campaign/get/", "campaign/get/?token=x"):
            with self.assertRaises(Exception):
                endpoint_for("GET", path)
        session = requests.Session()
        with patch("requests.sessions.get_netrc_auth", side_effect=AssertionError("netrc must not be read")), \
                patch.object(session, "send", return_value=response({"code": 0})) as send:
            TikTokHTTP("token", session=session).request(ENDPOINTS["campaign_get"])
        prepared = send.call_args.args[0]
        self.assertEqual(prepared.headers["Access-Token"], "token")
        self.assertNotIn("Authorization", prepared.headers)
        self.assertFalse(send.call_args.kwargs["allow_redirects"])

    def test_file_stream_and_url_upload_modes(self):
        session = Mock()
        chunks = []
        def consume(method, url, **options):
            if "data" in options:
                encoder = options["data"]
                while chunk := encoder.read(65536):
                    chunks.append(chunk)
            return response({"code": 0})
        session.request.side_effect = consume
        client = TikTokClient("token", transport=TikTokHTTP("token", session=session))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mp4"
            path.write_bytes(b"video-content")
            client.upload_video("123", str(path), auto_bind_enabled=False)
        wire = b"".join(chunks)
        self.assertIn(b"video-content", wire)
        self.assertIn(b"false", wire)
        self.assertTrue(session.request.call_args.kwargs["headers"]["Content-Type"].startswith("multipart/form-data;"))
        client.upload_video("123", video_url="https://cdn.example/video.mp4")
        self.assertEqual(session.request.call_args.kwargs["json"]["upload_type"], "UPLOAD_BY_URL")
        self.assertNotIn("data", session.request.call_args.kwargs)
        client.upload_image("123", image_url="https://cdn.example/image.jpg")
        self.assertEqual(session.request.call_args.kwargs["json"]["upload_type"], "UPLOAD_BY_URL")

    def test_upload_cli_accepts_url_without_a_file(self):
        from motata_cli.__main__ import build_parser
        parser = build_parser()
        for words in (("media", "upload-video"), ("videos", "upload")):
            args = parser.parse_args(["tiktok", *words, "--advertiser-id", "123", "--video-url", "https://cdn.example/a.mp4"])
            self.assertIsNone(args.file)

    def test_wrapper_queries_preserve_platform_id_filters(self):
        transport = Mock()
        transport.request.return_value = {"code": 0, "data": {"list": [{"campaign_id": "c", "adgroup_id": "g", "smart_plus_ad_id": "a"}]}}
        client = TikTokClient("fixture-token", transport=transport)
        for method, object_id, field in (("get_campaign", "c", "campaign_ids"), ("get_adgroup", "g", "adgroup_ids"), ("get_ad", "a", "smart_plus_ad_ids")):
            getattr(client, method)("123", object_id, smart_plus=True)
            self.assertEqual(transport.request.call_args.kwargs["params"]["filtering"], {field: [object_id]})

    def test_large_upload_memory_is_bounded(self):
        import tracemalloc
        peaks = []
        with tempfile.TemporaryDirectory() as directory:
            for mib in (50, 500):
                path = Path(directory) / "large.mp4"
                with path.open("wb") as handle:
                    handle.truncate(mib * 1024 * 1024)
                session = Mock()
                def consume(method, url, **options):
                    count = 0
                    while chunk := options["data"].read(65536):
                        count += len(chunk)
                    self.assertGreater(count, mib * 1024 * 1024)
                    return response({"code": 0})
                session.request.side_effect = consume
                tracemalloc.start()
                with TikTokHTTP("fixture-token", session=session) as transport:
                    transport.request(ENDPOINTS["ad_video_upload"], body={"advertiser_id": "123"}, files={"video_file": path})
                peaks.append(tracemalloc.get_traced_memory()[1])
                tracemalloc.stop()
        self.assertLess(max(peaks), 64 * 1024 * 1024)
        self.assertLess(abs(peaks[1] - peaks[0]), 64 * 1024 * 1024)

    def test_close_and_response_limit(self):
        session = Mock()
        session.request.return_value = response({"code": 0, "data": "large"})
        transport = TikTokHTTP("token", session=session, response_limit=5)
        with self.assertRaises(TikTokError):
            transport.request(ENDPOINTS["campaign_get"])
        transport.close()
        with self.assertRaises(Exception):
            transport.request(ENDPOINTS["campaign_get"])


if __name__ == "__main__":
    unittest.main()
