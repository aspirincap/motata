"""Read-only retries, server delays, credentials and retry ownership regressions."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from motata_cli.__main__ import build_parser
from motata_cli.common.errors import CliError
from motata_cli.common.output import error_kind, request_with_retry
from motata_cli.report.tiktok import TikTokReportRunner, _pull_basic_report
from motata_cli.tiktok.client import TikTokClient
from motata_cli.tiktok.commands import resolve_tiktok_client
from motata_cli.tiktok.endpoints import ENDPOINTS
from motata_cli.tiktok.errors import TikTokError, check_response
from motata_cli.tiktok.retry import ReadRetryPolicy, retry_after_seconds
from motata_cli.transport.tiktok_http import TikTokHTTP


def reply(code=0, *, status=200, retry_after=None, body=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({'code': code, 'data': {} } if body is None else body).encode()
    response._content_consumed = True
    if retry_after is not None:
        response.headers['Retry-After'] = retry_after
    response.close = Mock()
    return response


class RetryPolicyTests(unittest.TestCase):
    def test_40100_numeric_and_string_are_rate_limits_without_message_heuristics(self):
        for code in (40100, '40100', 40108, 40109):
            with self.subTest(code=code), self.assertRaises(TikTokError) as caught:
                check_response({'code': code, 'message': 'request rejected'})
            self.assertEqual(caught.exception.category, 'rate_limit')
            self.assertEqual(error_kind(caught.exception), 'rate_limit')
        self.assertEqual(error_kind(RuntimeError('Error Code: 40100')), 'rate_limit')

    def test_auth_and_metrics_are_not_reclassified_as_transient(self):
        for body, status, expected in (({'code': 40105}, 200, 'auth'),
                                      ({'code': 40001, 'message': 'access token expired'}, 200, 'auth'),
                                      ({'code': 40001}, 403, 'auth'),
                                      ({'code': 40002, 'message': 'invalid metric'}, 200, 'metrics')):
            with self.subTest(body=body), self.assertRaises(TikTokError) as caught:
                check_response(body, status=status)
            self.assertEqual(caught.exception.category, expected)

    def test_retry_after_seconds_dates_and_past_dates(self):
        self.assertEqual(retry_after_seconds('120'), 120)
        self.assertEqual(retry_after_seconds('0.5'), .5)
        self.assertEqual(retry_after_seconds('Thu, 01 Jan 1970 00:02:00 GMT', now=30), 90)
        self.assertEqual(retry_after_seconds('Thu, 01 Jan 1970 00:00:10 GMT', now=30), 0)

    def test_invalid_retry_after_is_ignored(self):
        for value in (None, '', 'bad date', '-1', 'nan', 'inf', '-inf', float('nan'), True, []):
            with self.subTest(value=value):
                self.assertIsNone(retry_after_seconds(value, now=0))

    def test_exponential_equal_jitter_and_cap(self):
        policy = ReadRetryPolicy()
        for attempt, bounds in ((1, (5, 10)), (2, (10, 20)), (3, (20, 40)), (4, (30, 60)), (8, (30, 60))):
            self.assertEqual(policy.delay(attempt, uniform=lambda a, b: a), bounds[0])
            self.assertEqual(policy.delay(attempt, uniform=lambda a, b: b), bounds[1])
        sampler = Mock(return_value=2)
        self.assertEqual(policy.delay(1, uniform=sampler), 7)
        sampler.assert_called_once_with(0, 5)

    def test_server_minimum_is_not_clamped_or_jittered_down(self):
        policy = ReadRetryPolicy()
        self.assertEqual(policy.delay(1, '120', uniform=lambda a, b: a), 120)
        self.assertEqual(policy.delay(1, '120', uniform=lambda a, b: b), 125)
        self.assertEqual(policy.delay(1, 'Thu, 01 Jan 1970 00:02:00 GMT', now=30, uniform=lambda a, b: a), 90)
        self.assertEqual(policy.delay(1, 'bad header', uniform=lambda a, b: a), 5)

    def test_invalid_policies_fail_locally(self):
        for kwargs in ({'max_attempts': 0}, {'max_attempts': 11}, {'max_attempts': True},
                       {'max_attempts': 1.5}, {'base_delay': -1}, {'base_delay': float('nan')},
                       {'max_wait': float('inf')}, {'max_delay': '10'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ReadRetryPolicy(**kwargs)


class TransportRetryTests(unittest.TestCase):
    def setUp(self):
        self.limiter = patch('motata_cli.transport.tiktok_http.default_read_throttle').start().return_value
        self.limiter.acquire_delay.return_value = 0.0
        self.session = Mock()
        self.transport = TikTokHTTP('fixture-secret', session=self.session)
        self.client = TikTokClient('fixture-secret', transport=self.transport)
        self.sleep = patch('motata_cli.transport.tiktok_http.time.sleep').start()
        self.random = patch('motata_cli.tiktok.retry.random.uniform', return_value=0).start()
        self.addCleanup(patch.stopall)

    def test_success_has_no_delay(self):
        self.session.request.return_value = reply()
        self.client.list_campaigns('123')
        self.session.request.assert_called_once()
        self.sleep.assert_not_called()

    def test_read_get_and_reviewed_post_retry_identical_payloads(self):
        for endpoint in ('smart_plus_adgroup_get', 'tool_targeting_search'):
            with self.subTest(endpoint=endpoint):
                self.session.reset_mock()
                self.sleep.reset_mock()
                failure, success = reply(40100), reply()
                self.session.request.side_effect = [failure, success]
                kwargs = {'params': {'advertiser_id': '123', 'page': 1}} if ENDPOINTS[endpoint].method == 'GET' else {
                    'body': {'advertiser_id': '123', 'filter': {'false': False, 'clear': []}}}
                self.sleep.side_effect = lambda delay: failure.close.assert_called_once()
                self.transport.request(ENDPOINTS[endpoint], **kwargs)
                self.assertEqual(self.session.request.call_count, 2)
                first, second = self.session.request.call_args_list
                self.assertEqual(first.args, second.args)
                self.assertEqual({k: v for k, v in first.kwargs.items() if k != 'auth'},
                                 {k: v for k, v in second.kwargs.items() if k != 'auth'})
                self.sleep.assert_called_once_with(5)

    def test_non_json_and_malformed_429_respect_retry_after(self):
        for raw in (b'<html>rate limited</html>', b'{}', b'[]'):
            self.sleep.reset_mock()
            failure = reply(status=429, retry_after='90')
            failure._content = raw
            self.session.request.side_effect = [failure, reply()]
            self.client.list_campaigns('123')
            self.sleep.assert_called_once_with(90)
            failure.close.assert_called_once()

    def test_http_date_header_used_by_transport(self):
        self.session.request.side_effect = [reply(40100, retry_after='Thu, 01 Jan 1970 00:02:00 GMT'), reply()]
        with patch('motata_cli.tiktok.retry.time.time', return_value=30):
            self.client.list_campaigns('123')
        self.sleep.assert_called_once_with(90)

    def test_network_and_server_errors_retry_reads(self):
        for failure in (requests.ConnectTimeout('fixture-secret'), requests.ReadTimeout('fixture-secret'),
                        requests.ConnectionError('fixture-secret'), reply(500, status=503), reply(status=503, body={})):
            with self.subTest(failure=type(failure).__name__):
                self.session.reset_mock()
                self.sleep.reset_mock()
                self.session.request.side_effect = [failure, reply()]
                self.client.list_campaigns('123')
                self.assertEqual(self.session.request.call_count, 2)
                self.sleep.assert_called_once_with(5)

    def test_auth_parameter_and_protocol_errors_are_single_attempt(self):
        for failure in (reply(40105), reply(40002), reply(body={}), reply(0, status=302)):
            self.session.reset_mock()
            self.session.request.side_effect = None
            self.session.request.return_value = failure
            with self.assertRaises(TikTokError):
                self.client.list_campaigns('123')
            self.session.request.assert_called_once()
        self.sleep.assert_not_called()

    def test_exhaustion_is_not_multiplied_by_generic_retry_wrapper(self):
        self.session.request.side_effect = lambda *a, **kw: reply(40100)
        with self.assertRaises(TikTokError) as caught:
            request_with_retry(self.client.list_campaigns, '123')
        self.assertEqual(self.session.request.call_count, 5)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [5, 10, 20, 30])
        self.assertEqual(caught.exception.attempts, 5)
        self.assertTrue(caught.exception.retry_managed)
        self.assertEqual(caught.exception.retry_stop_reason, 'attempt_limit')

    def test_retry_after_beyond_budget_stops_without_early_retry(self):
        self.session.request.return_value = reply(40100, retry_after='181')
        with self.assertRaises(TikTokError) as caught:
            request_with_retry(self.client.list_campaigns, '123')
        self.session.request.assert_called_once()
        self.sleep.assert_not_called()
        self.assertEqual(caught.exception.retry_stop_reason, 'wait_budget')
        self.assertEqual(caught.exception.retry_after, '181')

    def test_cumulative_wait_budget_is_shared_by_all_attempts(self):
        self.transport.retry_policy = ReadRetryPolicy(max_wait=15)
        self.session.request.side_effect = lambda *a, **kw: reply(40100)
        with self.assertRaises(TikTokError) as caught:
            self.client.list_campaigns('123')
        self.assertEqual(self.session.request.call_count, 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [5, 10])
        self.assertEqual(caught.exception.retry_stop_reason, 'wait_budget')

    def test_wait_budget_resets_for_an_independent_read(self):
        self.transport.retry_policy = ReadRetryPolicy(max_wait=5)
        self.session.request.side_effect = [reply(40100), reply(), reply(40100), reply()]
        self.client.list_campaigns('123')
        self.client.list_campaigns('456')
        self.assertEqual(self.session.request.call_count, 4)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [5, 5])

    def test_one_attempt_disables_retries_without_adding_outer_attempts(self):
        self.transport.retry_policy = ReadRetryPolicy(max_attempts=1)
        self.session.request.return_value = reply(40100)
        with self.assertRaises(TikTokError):
            request_with_retry(self.client.list_campaigns, '123')
        self.session.request.assert_called_once()
        self.sleep.assert_not_called()

    def test_cancel_during_backoff_stops_without_reissuing_request(self):
        failure = reply(40100)
        self.session.request.side_effect = [failure, reply()]
        self.sleep.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.client.list_campaigns('123')
        self.session.request.assert_called_once()
        failure.close.assert_called_once()

    def test_all_registered_writes_never_retry_even_through_outer_helper(self):
        for name, endpoint in ENDPOINTS.items():
            if not endpoint.write:
                continue
            for failure in (reply(40100), reply(body={'code': 40002, 'message': 'OVER_QPS_LIMIT'}),
                            requests.ConnectTimeout(), requests.ReadTimeout(), reply(500, status=503)):
                with self.subTest(endpoint=name, failure=type(failure).__name__):
                    self.session.reset_mock()
                    self.session.request.side_effect = [failure]
                    with self.assertRaises(TikTokError):
                        request_with_retry(self.transport.request, endpoint, body={})
                    self.session.request.assert_called_once()
        self.sleep.assert_not_called()

        self.limiter.acquire_delay.assert_not_called()
        self.limiter.defer.assert_not_called()

    def test_upload_stream_is_not_reopened_or_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / 'video.mp4'
            media.write_bytes(b'fixture-video')
            self.session.request.return_value = reply(40100)
            with self.assertRaises(TikTokError):
                self.client.upload_video('123', str(media))
            self.session.request.assert_called_once()
            self.sleep.assert_not_called()

    def test_retry_logs_do_not_contain_credentials_or_payload(self):
        self.session.request.side_effect = [reply(40100, body={'code': 40100, 'message': 'fixture-secret'}), reply()]
        with self.assertLogs('motata_cli.transport.tiktok_http', level='WARNING') as logs:
            self.client.list_campaigns('private-account', filtering={'campaign_name': 'private-name'})
        self.assertIn('retry 2/5', logs.output[0])
        for value in ('fixture-secret', 'private-account', 'private-name'):
            self.assertNotIn(value, str(logs.output))

    def test_report_source_never_replays_completed_pages(self):
        def request(method, url, **options):
            if options['params']['page'] == '1':
                return reply(body={'code': 0, 'data': {'list': [{'metrics': {'spend': '1'}}], 'page_info': {'total_page': 2}}})
            return reply(40100)
        self.session.request.side_effect = request
        with tempfile.TemporaryDirectory() as directory:
            runner = object.__new__(TikTokReportRunner)
            runner.run_dir = Path(directory)
            runner.manifest = {'sources': []}
            runner.args = argparse.Namespace(access_token='fixture-secret', retry=9, retry_wait=0)
            result = runner.run_source('report', lambda: _pull_basic_report(self.client, '123', 'advertiser',
                ('2026-11-01', '2026-11-02'), page_size=1, max_pages=2))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual([c.kwargs['params']['page'] for c in self.session.request.call_args_list], ['1', '2', '2', '2', '2', '2'])
            self.assertEqual(runner.manifest['sources'][0]['attempts'], 1)
            self.assertEqual(runner.manifest['sources'][0]['http_attempts'], 5)

    def test_report_flags_configure_single_http_budget_and_leave_meta_defaults(self):
        parser = build_parser()
        args = parser.parse_args(['report', 'tiktok', 'run', '--account', '123', '--access-token', 'fixture-secret',
                                  '--retry', '3', '--retry-wait', '8'])
        _, client = resolve_tiktok_client(args)
        self.addCleanup(client.close)
        self.assertEqual(client.transport.retry_policy, ReadRetryPolicy(max_attempts=3, base_delay=8))
        defaults = parser.parse_args(['report', 'tiktok', 'run'])
        meta = parser.parse_args(['report', 'meta', 'run'])
        self.assertEqual((defaults.retry, defaults.retry_wait), (5, 10))
        self.assertEqual((meta.retry, meta.retry_wait), (2, 60))
        args.retry = 0
        with self.assertRaises(CliError):
            resolve_tiktok_client(args)


if __name__ == '__main__':
    unittest.main()
