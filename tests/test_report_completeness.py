from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from motata_cli.meta.commands import CliError
from motata_cli.report.completeness import completeness, finalize
from motata_cli.tiktok.landing_pages import _report_rows, build_tiktok_landing_page_report


class ReportCompletenessTests(unittest.TestCase):
    def pull(self, client, **kwargs):
        return _report_rows(client, 'a', start_date='2026-01-01', end_date='2026-01-02',
                            page_size=1, max_pages=1, **kwargs)

    def test_page_cap_with_and_without_total(self):
        for info in ({}, {'total_page': 2}):
            client = Mock()
            client.integrated_report.return_value = {'data': {'list': [{}], 'page_info': info}}
            rows, metadata = self.pull(client)
            self.assertEqual(len(rows), 1)
            self.assertTrue(metadata['truncated'])
            self.assertEqual(metadata['stop_reason'], 'max_pages')
            self.assertEqual(metadata['pages_fetched'], 1)

    def test_auth_and_non_metric_errors_never_fallback(self):
        for error in ('403 invalid metric permission', 'invalid dimension', 'invalid request metrics'):
            client = Mock()
            client.integrated_report.side_effect = CliError(error)
            with self.assertRaises(CliError):
                self.pull(client)
            self.assertEqual(client.integrated_report.call_count, 1)

    @patch('motata_cli.common.output.time.sleep')
    def test_network_retries_same_parameters_bounded(self, sleep):
        client = Mock()
        client.integrated_report.side_effect = TimeoutError('timed out')
        with self.assertRaises(TimeoutError):
            self.pull(client)
        self.assertEqual(client.integrated_report.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(len(set(str(call) for call in client.integrated_report.call_args_list)), 1)

    def test_explicit_metric_error_allows_fallback(self):
        client = Mock()
        client.integrated_report.side_effect = [CliError('metric advertiser_name not supported'), {'data': {'list': []}}]
        _, metadata = self.pull(client)
        self.assertEqual(len(metadata['attempts']), 1)
        self.assertEqual(client.integrated_report.call_count, 2)

    def test_currency_url_groups_and_preload_truncation(self):
        rows = {}
        for account, currency in [('a', 'USD'), ('b', 'usd'), ('c', 'EUR'), ('d', '-'), ('e', None)]:
            rows[account] = [{'dimensions': {'ad_id': account}, 'metrics': {
                'spend': '10', 'currency': currency, 'ad_url': 'https://example.com/products/a'}}]
        result = build_tiktok_landing_page_report(object(), advertiser_ids=list(rows),
            start_date='2026-01-01', end_date='2026-01-02', preloaded_report_rows=rows,
            preloaded_report_metadata={'ad': {'truncated': True, 'stop_reason': 'max_pages'}})
        self.assertEqual(len(result['rows']), 4)
        self.assertEqual(next(r['spend'] for r in result['rows'] if r['currency'] == 'USD'), 20)
        self.assertEqual(sum(r['currency'] is None for r in result['rows']), 2)
        self.assertIsNone(result['total_spend'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['completeness']['status'], 'partial_success')

    def test_nested_and_batch_status_contract(self):
        ok = finalize({'sources': [{'status': 'success'}]})
        failed = finalize({'sources': [{'status': 'failed', 'error': 'denied'}]})
        partial = finalize({'sources': [{'status': 'success'}, {'status': 'failed'}]})
        for payload, status, code in [(ok, 'success', 0), (failed, 'failed', 1), (partial, 'partial_success', 3)]:
            self.assertEqual(payload['completeness']['status'], status)
            self.assertEqual(payload['completeness']['exit_code'], code)
        self.assertEqual(completeness({'runs': [ok, failed]})['status'], 'partial_success')
        self.assertEqual(completeness({'runs': [failed, failed]})['status'], 'failed')
