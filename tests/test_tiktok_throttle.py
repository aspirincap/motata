"""Real SQLite coordination with fake HTTP, clocks, and isolated child processes."""
from concurrent.futures import ThreadPoolExecutor
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import motata_cli

from motata_cli.common.errors import CliError
from motata_cli.common.output import error_kind, request_with_retry
from motata_cli.tiktok.endpoints import ENDPOINTS, Endpoint
from motata_cli.tiktok.errors import TikTokError, check_response
from motata_cli.tiktok.retry import ReadRetryPolicy
from motata_cli.tiktok.throttle import ReadThrottle
from motata_cli.transport.tiktok_http import TikTokHTTP

READ = ENDPOINTS['smart_plus_adgroup_get']
REVIEW = Endpoint('GET', 'smart_plus/ad/review_info/', False)


def reply(code=0, message='', retry_after=None):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({'code': code, 'message': message, 'data': {}}).encode()
    response._content_consumed = True
    if retry_after is not None:
        response.headers['Retry-After'] = str(retry_after)
    response.close = Mock()
    return response


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.waits = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


class QpsClassificationTests(unittest.TestCase):
    def test_40002_requires_the_specific_qps_message(self):
        for code in (40002, '40002'):
            for message in ('OVER_QPS_LIMIT', ' over_qps_limit. \n'):
                with self.subTest(code=code, message=message), self.assertRaises(TikTokError) as caught:
                    check_response({'code': code, 'message': message, 'request_id': 'fixture-trace'})
                error = caught.exception
                self.assertEqual(error.category, 'rate_limit')
                self.assertEqual(error_kind(error), 'rate_limit')
                self.assertEqual(error.platform_code, code)
                self.assertEqual(error.platform_request_id, 'fixture-trace')

    def test_other_40002_and_qps_like_messages_stay_nontransient(self):
        for code, message in ((40002, 'Invalid parameter'), (40002, 'refresh_token is permanently invalid'),
                              (40002, 'Missing field OVER_QPS_LIMIT'), (40002, 'OVER_QPS_LIMIT_EXTRA'),
                              (40002, ['OVER_QPS_LIMIT']), (40001, 'OVER_QPS_LIMIT')):
            with self.subTest(code=code, message=message), self.assertRaises(TikTokError) as caught:
                check_response({'code': code, 'message': message})
            self.assertNotIn(caught.exception.category, ('rate_limit', 'network'))


class SharedThrottleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'limits.sqlite3'
        self.clock = Clock()
        self.limiter = ReadThrottle(self.path, clock=self.clock)
        sleeper = patch('motata_cli.transport.tiktok_http.time.sleep', side_effect=self.clock.sleep)
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)
        jitter = patch('motata_cli.tiktok.retry.random.uniform', return_value=0)
        jitter.start()
        self.addCleanup(jitter.stop)

    def transport(self, *responses, token='fixture-secret', policy=None, limiter=None):
        session = Mock()
        session.request.side_effect = list(responses)
        client = TikTokHTTP(token, session=session, retry_policy=policy,
                            read_throttle=limiter if limiter is not None else self.limiter)
        self.addCleanup(client.close)
        return client, session

    def child(self, action):
        # Import the same package as the parent, including in installed smoke tests.
        code = ('import sys; sys.path.insert(0, sys.argv[2]); '
                'from motata_cli.tiktok.throttle import ReadThrottle; '
                'from motata_cli.tiktok.endpoints import ENDPOINTS; '
                'limiter = ReadThrottle(sys.argv[1], clock=lambda: 1000.0); '
                'endpoint = ENDPOINTS["smart_plus_adgroup_get"]; ' + action)
        result = subprocess.run([sys.executable, '-c', code, str(self.path),
                                 str(Path(motata_cli.__file__).resolve().parent.parent)],
                                cwd=self.directory.name, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_atomic_admission_across_independent_processes(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            values = list(pool.map(lambda _: self.child('print(limiter.acquire_delay(endpoint))'), range(5)))
        self.assertEqual(sorted(map(float, values)), [0, 1, 1, 1, 1])

    def test_child_cooldown_is_visible_after_reopening(self):
        self.child('limiter.defer(endpoint, 37)')
        reopened = ReadThrottle(self.path, clock=self.clock)
        self.assertEqual(reopened.acquire_delay(READ), 37)
        self.clock.sleep(37)
        self.assertEqual(reopened.acquire_delay(READ), 0)

    def test_different_endpoints_have_independent_pacing(self):
        other = ReadThrottle(self.path, clock=self.clock)
        self.assertEqual(self.limiter.acquire_delay(READ), 0)
        self.assertEqual(other.acquire_delay(READ), 1)
        self.assertEqual(other.acquire_delay(REVIEW), 0)
        self.assertEqual(self.limiter.acquire_delay(REVIEW), 2)
        self.limiter.defer(READ, 30)
        self.assertEqual(other.acquire_delay(REVIEW), 2)

    def test_cooldown_can_only_be_extended(self):
        self.limiter.defer(READ, 40)
        self.clock.sleep(5)
        self.limiter.defer(READ, 10)
        self.assertEqual(self.limiter.acquire_delay(READ), 35)
        self.limiter.defer(READ, 50)
        self.assertEqual(self.limiter.acquire_delay(READ), 50)

    def test_no_lock_or_long_reservation_is_left_by_an_abandoned_reader(self):
        self.child('print(limiter.acquire_delay(endpoint))')
        self.clock.sleep(1)
        self.assertEqual(self.limiter.acquire_delay(READ), 0)

    def test_qps_retry_closes_response_and_waits_only_once(self):
        failure = reply(40002, 'OVER_QPS_LIMIT')
        client, session = self.transport(failure, reply())
        def wait(delay):
            failure.close.assert_called_once()
            self.clock.sleep(delay)
        self.sleep.side_effect = wait
        request_with_retry(client.request, READ, params={'advertiser_id': 'fixture-account'})
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(self.clock.waits, [5])
        self.assertEqual(session.request.call_args_list[0].kwargs['params'],
                         session.request.call_args_list[1].kwargs['params'])

    def test_qps_exhaustion_is_not_multiplied_by_outer_retry(self):
        client, session = self.transport(*(reply(40002, 'OVER_QPS_LIMIT') for _ in range(5)))
        with self.assertRaises(TikTokError) as caught:
            request_with_retry(client.request, READ)
        self.assertEqual(session.request.call_count, 5)
        self.assertEqual(self.clock.waits, [5, 10, 20, 30])
        self.assertEqual((caught.exception.attempts, caught.exception.retry_stop_reason), (5, 'attempt_limit'))
        self.assertEqual(self.limiter.acquire_delay(READ), 30)

    def test_accounts_and_tokens_share_the_same_endpoint_bucket(self):
        first, _ = self.transport(reply(), token='secret-one')
        second, _ = self.transport(reply(), token='secret-two', limiter=ReadThrottle(self.path, clock=self.clock))
        with self.assertLogs('motata_cli.transport.tiktok_http', level='WARNING') as logs:
            first.request(READ, params={'advertiser_id': 'account-one'})
            second.request(READ, params={'advertiser_id': 'account-two'})
        self.assertEqual(self.clock.waits, [1])
        stored = self.path.read_bytes()
        for value in ('secret-one', 'secret-two', 'account-one', 'account-two'):
            self.assertNotIn(value.encode(), stored)
            self.assertNotIn(value, str(logs.output))
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_read_only_post_is_paced_and_retried(self):
        endpoint = ENDPOINTS['tool_targeting_search']
        client, session = self.transport(reply(), reply(40002, 'OVER_QPS_LIMIT'), reply())
        payload = {'advertiser_id': 'fixture-account', 'filter': {'false': False}}
        client.request(endpoint, body=payload)
        client.request(endpoint, body=payload)
        self.assertEqual(self.clock.waits, [1, 5])
        self.assertTrue(all(call.args[0] == 'POST' and call.kwargs['json'] == payload
                            for call in session.request.call_args_list))

    def test_wait_rechecks_a_concurrent_cooldown_extension(self):
        self.limiter.defer(READ, 5)
        other = ReadThrottle(self.path, clock=self.clock)
        client, session = self.transport(reply())
        def wait(delay):
            self.clock.sleep(delay)
            if len(self.clock.waits) == 1:
                other.defer(READ, 20)
        self.sleep.side_effect = wait
        client.request(READ)
        self.assertEqual(self.clock.waits, [5, 20])
        session.request.assert_called_once()

    def test_an_inflight_success_does_not_clear_another_readers_cooldown(self):
        client, session = self.transport()
        def success(*args, **kwargs):
            ReadThrottle(self.path, clock=self.clock).defer(READ, 45)
            return reply()
        session.request.side_effect = success
        client.request(READ)
        self.assertEqual(self.limiter.acquire_delay(READ), 45)

    def test_final_failure_publishes_cooldown_for_next_client(self):
        first, session = self.transport(reply(40002, 'OVER_QPS_LIMIT'), policy=ReadRetryPolicy(max_attempts=1))
        with self.assertRaises(TikTokError):
            first.request(READ)
        session.request.assert_called_once()
        self.sleep.assert_not_called()
        second, session2 = self.transport(reply(), limiter=ReadThrottle(self.path, clock=self.clock))
        second.request(READ)
        self.assertEqual(self.clock.waits, [5])
        session2.request.assert_called_once()

    def test_retry_after_over_budget_is_shared_without_sending_next_request(self):
        first, session = self.transport(reply(40002, 'OVER_QPS_LIMIT', retry_after=181))
        with self.assertRaises(TikTokError) as caught:
            first.request(READ)
        self.assertEqual(caught.exception.retry_after, '181')
        self.assertEqual(caught.exception.retry_stop_reason, 'wait_budget')
        session.request.assert_called_once()
        second, session2 = self.transport(reply(), policy=ReadRetryPolicy(max_wait=0))
        with self.assertRaises(TikTokError) as stopped:
            request_with_retry(second.request, READ)
        self.assertEqual((stopped.exception.attempts, stopped.exception.retry_stop_reason), (0, 'wait_budget'))
        session2.request.assert_not_called()
        self.sleep.assert_not_called()

    def test_pacing_and_backoff_share_one_wait_budget(self):
        self.limiter.defer(READ, 4)
        client, session = self.transport(reply(40002, 'OVER_QPS_LIMIT'), reply(), policy=ReadRetryPolicy(max_wait=8))
        with self.assertRaises(TikTokError) as caught:
            client.request(READ)
        self.assertEqual(self.clock.waits, [4])
        session.request.assert_called_once()
        self.assertEqual((caught.exception.attempts, caught.exception.retry_stop_reason), (1, 'wait_budget'))

    def test_network_failure_does_not_publish_shared_rate_cooldown(self):
        client, _ = self.transport(requests.ConnectTimeout(), reply())
        client.request(READ)
        self.assertEqual(self.clock.waits, [5])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT cooldown_until FROM endpoint_limits').fetchone()[0], 0)

    def test_cancel_during_shared_wait_sends_nothing(self):
        self.limiter.defer(READ, 5)
        self.sleep.side_effect = KeyboardInterrupt()
        client, session = self.transport(reply())
        with self.assertRaises(KeyboardInterrupt):
            client.request(READ)
        session.request.assert_not_called()
        self.assertEqual(self.limiter.acquire_delay(READ), 5)

    def test_corrupt_state_fails_before_http_without_outer_retry(self):
        self.path.write_bytes(b'not a sqlite database')
        client, session = self.transport(reply())
        with self.assertRaises(CliError) as caught:
            request_with_retry(client.request, READ)
        self.assertEqual(caught.exception.retry_stop_reason, 'limiter_error')
        self.assertEqual(caught.exception.attempts, 0)
        self.assertNotIn(str(self.path), str(caught.exception))
        session.request.assert_not_called()
        self.sleep.assert_not_called()

    def test_unavailable_default_state_fails_closed(self):
        client, session = self.transport(reply())
        client.read_throttle = None
        with patch('motata_cli.tiktok.throttle.CACHE_DIR', self.path), self.assertRaises(CliError):
            client.request(READ)
        session.request.assert_not_called()

    def test_corrupt_numeric_state_is_rejected(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO endpoint_limits VALUES (?, ?, ?)', (self.limiter._key(READ), 0, 'invalid'))
        client, session = self.transport(reply())
        with self.assertRaises(CliError) as caught:
            client.request(READ)
        self.assertEqual(caught.exception.retry_stop_reason, 'limiter_error')
        session.request.assert_not_called()

    def test_state_failure_after_rate_response_preserves_original_error(self):
        client, session = self.transport()
        def failure(*args, **kwargs):
            self.path.write_bytes(b'corrupt')
            return reply(40002, 'OVER_QPS_LIMIT')
        session.request.side_effect = failure
        with self.assertRaises(TikTokError) as caught:
            client.request(READ)
        self.assertEqual(caught.exception.platform_code, 40002)
        self.assertEqual((caught.exception.attempts, caught.exception.retry_stop_reason), (1, 'limiter_error'))
        session.request.assert_called_once()
        self.sleep.assert_not_called()

    def test_writes_and_uploads_do_not_touch_shared_state(self):
        self.path.write_bytes(b'corrupt')
        media = Path(self.directory.name) / 'fixture.mp4'
        media.write_bytes(b'fixture')
        for endpoint, files in ((ENDPOINTS['campaign_create'], None),
                                (ENDPOINTS['ad_video_upload'], {'video_file': str(media)})):
            with self.subTest(path=endpoint.path):
                client, session = self.transport(reply(40002, 'OVER_QPS_LIMIT'))
                with self.assertRaises(TikTokError) as caught:
                    request_with_retry(client.request, endpoint, body={}, files=files)
                self.assertEqual(caught.exception.platform_code, 40002)
                session.request.assert_called_once()
        self.sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
