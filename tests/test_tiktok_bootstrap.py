"""Offline copy/bootstrap regression tests; no credentials or runtime directories."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from motata_cli import __main__ as cli
from motata_cli.tiktok import commands as commands


class TikTokBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.source = ({'campaign_id': 'source', 'objective_type': 'APP_PROMOTION'}, True,
                       [{'adgroup_id': 'source-group'}],
                       {'source-group': [{'ad_id': 'source-ad'}]}, {'target_smart_plus': True})
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('socket.socket.connect',
                                      side_effect=AssertionError('Network forbidden in offline tests')))
        self.stack.enter_context(patch.object(commands, 'resolve_tiktok_client', return_value=('123', self.client)))
        self.stack.enter_context(patch.object(commands, 'load_tiktok_campaign_copy_source', return_value=self.source))
        for name in ('build_campaign_copy_payload', 'build_adgroup_copy_payload',
                     'build_normal_ad_copy_payload', 'build_smart_plus_ad_copy_payload'):
            self.stack.enter_context(patch.object(commands, name, return_value={'campaign_name': 'copy'}))
        self.stack.enter_context(patch.object(commands, 'infer_adgroup_copy_strategy', return_value={}))
        self.stack.enter_context(patch.object(commands, 'infer_ad_copy_strategy', return_value={}))
        self.stack.enter_context(patch.object(commands, 'find_tiktok_smartplus_app_template',
                                            return_value={'template_campaign': {'campaign_id': 'source'}}))
        self.output = self.stack.enter_context(patch.object(commands, 'print_output'))
        self.client.create_campaign.return_value = {'data': {'campaign_id': 'new-campaign'}}
        self.client.create_adgroup.return_value = {'data': {'adgroup_id': 'new-group'}}
        self.client.create_ad.return_value = {'data': {'ad_ids': ['new-ad']}}

    def copy(self, **overrides):
        options = dict(advertiser_id='123', campaign_id='source', name='copy', copies=1,
                       operation_status='DISABLE', adgroup_status='DISABLE', ad_status='DISABLE',
                       landing_page_url=None, page_id=None, skip_adgroups=False, skip_ads=False,
                       smart_plus=True, verbose=False)
        options.update(overrides)
        return commands.execute_tiktok_campaign_copy(self.client, **options)

    def argv(self, command='bootstrap-app', *extra):
        return ['tiktok', 'smartplus-campaigns', command, '--advertiser-id', '123',
                '--name', 'copy', *(['source'] if command == 'copy' else []), *extra]

    def test_success_retains_created_children(self):
        result = self.copy()
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['ok'])
        self.assertEqual(result['campaigns'][0]['adgroups'][0]['adgroup_id'], 'new-group')
        self.assertEqual(result['campaigns'][0]['ads'][0]['response']['ad_ids'], ['new-ad'])

    def test_missing_campaign_id_counted_once(self):
        self.client.create_campaign.return_value = {'message': 'denied'}
        result = self.copy(copies=2)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['ok'])
        self.assertEqual(result['copies_failed'], 2)
        self.client.create_adgroup.assert_not_called()

    def test_mixed_campaign_failure(self):
        self.client.create_campaign.side_effect = [commands.CliError('denied'), {'data': {'campaign_id': 'kept'}}]
        result = self.copy(copies=2)
        self.assertEqual(result['status'], 'partial_success')
        self.assertFalse(result['ok'])
        self.assertEqual(result['campaigns'][0]['campaign_id'], 'kept')
        self.assertEqual(result['failed'][0]['error'], 'denied')

    def test_child_failures_are_visible_for_missing_ids_and_exceptions(self):
        for kind in ('adgroup', 'ad'):
            for response in ({'message': 'denied'}, commands.CliError('denied')):
                with self.subTest(kind=kind, response=response):
                    method = getattr(self.client, 'create_' + kind)
                    method.side_effect = response if isinstance(response, Exception) else None
                    method.return_value = response
                    result = self.copy()
                    self.assertEqual(result['status'], 'partial_success')
                    self.assertEqual(result['campaigns'][0][kind + 's_failed'], 1)
                    self.assertEqual(result['campaigns'][0]['errors'][0]['error'], 'denied')
                    method.side_effect = None
                    method.return_value = {'data': {kind + '_id': 'restored'}}

    def test_zero_copies_rejected_before_reading(self):
        with self.assertRaises(commands.CliError):
            self.copy(copies=0)
        commands.load_tiktok_campaign_copy_source.assert_not_called()

    def test_skip_flags_never_create_children(self):
        self.copy(skip_adgroups=True)
        self.client.create_adgroup.assert_not_called()
        self.client.create_ad.assert_not_called()
        self.copy(skip_ads=True)
        self.client.create_ad.assert_not_called()

    def test_dry_run_auto_and_explicit_template_never_write(self):
        for extra in ([], ['--template-campaign-id', 'source']):
            with self.subTest(extra=extra), patch.object(commands, 'execute_tiktok_campaign_copy') as execute:
                args = cli.build_parser().parse_args(self.argv('bootstrap-app', '--dry-run', *extra))
                args.func(args)
                result = self.output.call_args.args[0]
                self.assertTrue(result['ok'])
                self.assertEqual(result['mode'], 'dry_run')
                self.assertFalse(result['writes_performed'])
                execute.assert_not_called()
        self.client.create_campaign.assert_not_called()
        self.client.create_adgroup.assert_not_called()
        self.client.create_ad.assert_not_called()

    def test_main_returns_nonzero_with_preserved_output(self):
        for command in ('copy', 'bootstrap-app'):
            for partial in (False, True):
                with self.subTest(command=command, partial=partial):
                    self.client.create_campaign.return_value = {'data': {'campaign_id': 'kept'}} if partial else {'message': 'denied'}
                    self.client.create_adgroup.return_value = {'message': 'child denied'}
                    with patch.object(cli, 'ensure_dirs'), patch.object(cli, 'maybe_emit_skills_drift_notice'), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(cli.main(self.argv(command)), 3 if partial else 1)
                    result = self.output.call_args.args[0]
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['status'], 'partial_success' if partial else 'failed')
                    copy_result = result.get('copy_result', result)
                    self.assertEqual(copy_result['copies_created'], int(partial))

    def test_cli_zero_copies_never_write(self):
        for command, extra in (('copy', []), ('bootstrap-app', []),
                               ('bootstrap-app', ['--dry-run'])):
            with self.subTest(command=command, extra=extra):
                args = cli.build_parser().parse_args(self.argv(command, '--copies', '0', *extra))
                with self.assertRaises(commands.CliError) as raised:
                    args.func(args)
                self.assertEqual(raised.exception.exit_code, 1)
        commands.load_tiktok_campaign_copy_source.assert_not_called()
        commands.find_tiktok_smartplus_app_template.assert_not_called()
        self.client.create_campaign.assert_not_called()
        self.client.create_adgroup.assert_not_called()
        self.client.create_ad.assert_not_called()

    def test_copy_defaults_disable_all_created_levels(self):
        for command in ('copy', 'bootstrap-app'):
            args = cli.build_parser().parse_args(self.argv(command))
            self.assertEqual((args.operation_status, args.adgroup_status, args.ad_status),
                             ('DISABLE', 'DISABLE', 'DISABLE'))

    def test_main_success(self):
        with patch.object(cli, 'ensure_dirs'), patch.object(cli, 'maybe_emit_skills_drift_notice'):
            self.assertEqual(cli.main(self.argv()), 0)
        self.assertEqual(self.output.call_args.args[0]['status'], 'success')


if __name__ == '__main__':
    unittest.main()
