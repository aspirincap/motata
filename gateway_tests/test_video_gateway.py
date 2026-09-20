"""Video phases through actual CLI clients; fake media and fake platform only."""
import asyncio
import json
from unittest.mock import patch
import httpx
import test_continuation as helpers
from motata_cli.transport.gateway import GatewayAuthRef
from motata_cli.common.errors import CliError
from motata_gateway.errors import GatewayError


class VideoAndRatePipelineTests(helpers.ContinuationTests):
    async def router(self, replies):
        await self.http.aclose()
        pending = iter(replies)
        async def handle(req):
            self.seen.append(req)
            value = next(pending)
            if isinstance(value, Exception): raise value
            return httpx.Response(200, json=value)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
        self.service.http = self.http

    async def test_meta_chunked_upload_start_transfer_finish(self):
        from motata_cli.meta.client import MetaClient
        from motata_cli.meta.services.media import chunked_upload_video
        video = self.root / 'demo.mp4'; video.write_bytes(b'abcdef')
        await self.router([
            {'upload_session_id': '901', 'video_id': '900', 'start_offset': '0', 'end_offset': '3'},
            {'start_offset': '3', 'end_offset': '6'},
            {'start_offset': '6', 'end_offset': '6'},
            {'success': True},
        ])
        result = await self.invoke_client(lambda _: chunked_upload_video(
            MetaClient(GatewayAuthRef('meta', '123'), version='v23.0'), '123', video, name='video', title=None))
        self.assertEqual(result['id'], '900')
        self.assertEqual(len(self.seen), 4)
        self.assertTrue(all(req.url.host == 'graph-video.facebook.com' for req in self.seen))
        self.assertIn(b'abc', self.seen[1].content)
        self.assertIn(b'def', self.seen[2].content)
        self.state.require_object('meta', '901', '123', 'upload_session')
        self.assertNotIn(self.meta_secret, json.dumps(result))
        self.assertEqual(list(self.service.uploads.root.iterdir()), [])

    async def test_meta_bad_transfer_offset_stops_without_replay(self):
        from motata_cli.meta.client import MetaClient
        from motata_cli.meta.services.media import chunked_upload_video
        video = self.root / 'demo.mp4'; video.write_bytes(b'abcdef')
        await self.router([
            {'upload_session_id': '901', 'video_id': '900', 'start_offset': '0', 'end_offset': '3'},
            {'start_offset': '1', 'end_offset': '6'},
        ])
        with self.assertRaises(CliError):
            await self.invoke_client(lambda _: chunked_upload_video(
                MetaClient(GatewayAuthRef('meta', '123'), version='v23.0'), '123', video, name=None, title=None))
        self.assertEqual(len(self.seen), 2)

    async def test_tiktok_sdk_video_upload_checks_request_and_identity(self):
        from motata_cli.tiktok.client import TikTokClient
        video = self.root / 'creative.mp4'; video.write_bytes(b'dummy-video' * 10000)
        self.reply = {'code': 0, 'data': [{'video_id': '902'}]}
        result = await self.invoke_client(lambda _: TikTokClient(GatewayAuthRef('tiktok', '123')).upload_video('123', str(video)))
        self.assertEqual(result['data'][0]['video_id'], '902')
        self.state.require_object('tiktok', '902', '123', 'video')
        self.assertIn(b'name="video_file"', self.seen[0].content)
        self.assertIn(video.read_bytes(), self.seen[0].content)
        self.assertNotIn(str(video).encode(), self.seen[0].content)
        self.assertNotIn(self.tiktok_secret, json.dumps(result))

    async def test_meta_single_video_source_is_stream_not_read_bytes(self):
        from motata_cli.meta.client import MetaClient
        from motata_cli.meta.services.media import single_upload_video
        video = self.root / 'demo.mp4'; video.write_bytes(b'video-data')
        self.reply = {'id': '903'}
        with patch('pathlib.Path.read_bytes', side_effect=AssertionError('whole file read forbidden')):
            result = await self.invoke_client(lambda _: single_upload_video(
                MetaClient(GatewayAuthRef('meta', '123'), version='v23.0'), '123', video, name=None, title=None))
        self.assertEqual(result['id'], '903')
        self.assertEqual(len(self.seen), 1)

    async def test_rate_wait_failure_creates_no_write_receipt(self):
        class RejectedBudget:
            async def wait(self, key):
                raise GatewayError('RATE_WAIT_TIMEOUT', 503, 'Test budget exceeded.', 'not_sent')
        self.service.rates = RejectedBudget()
        request = self.body(method='POST', query={}, body={'name': 'c', 'objective': 'OUTCOME_TRAFFIC'}, idempotency_key='rate-fail')
        response = await self.post(request)
        self.assertEqual(response.status_code, 503)
        self.assert_no_upstream()
        # The same write can be attempted for the first time once capacity exists.
        from motata_gateway.limits import RateBudget
        self.service.rates = RateBudget()
        self.reply = {'id': '904'}
        response = await self.post(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.seen), 1)

    async def test_jwt_expiry_after_rate_wait_never_fetches_secret(self):
        now = self.claims()['exp'] + 1
        time_patch = patch('motata_gateway.service.time.time', return_value=now)
        class ExpiredBudget:
            async def wait(self, key): time_patch.start()
        self.service.rates = ExpiredBudget()
        try:
            response = await self.post()
        finally:
            time_patch.stop()
        self.assertEqual(response.status_code, 401)
        self.assert_no_upstream()

    async def test_object_probe_rate_timeout_never_fetches_secret(self):
        class TimeoutBudget:
            async def wait(self, key):
                raise GatewayError('RATE_WAIT_TIMEOUT', 503, 'No capacity.', 'not_sent')
        self.service.rates = TimeoutBudget()
        response = await self.client.post('/v1/objects/resolve', json=self.body(path='456', query={}),
                                         headers={'Authorization': 'Bearer ' + self.token()})
        self.assertEqual(response.status_code, 503)
        self.assert_no_upstream()

# No imported/inherited baseline tests masquerading as new cases.
for _parent in VideoAndRatePipelineTests.__mro__[1:]:
    for _name in _parent.__dict__:
        if _name.startswith('test_') and _name not in VideoAndRatePipelineTests.__dict__:
            setattr(VideoAndRatePipelineTests, _name, None)
del _parent
