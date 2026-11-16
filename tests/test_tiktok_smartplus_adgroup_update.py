"""Reviewed incremental update behavior; platform reads/writes are mocked."""
import copy
import io
import json
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from motata_cli.__main__ import build_parser
from motata_cli.common.errors import CliError
from motata_cli.tiktok import commands
from motata_cli.tiktok.client import TikTokClient
from motata_cli.tiktok.smartplus_adgroup_update import validate_update, validate_context

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


class SmartPlusAdgroupUpdateTests(unittest.TestCase):
    def setUp(self):
        self.patch = {'advertiser_id': '123', 'adgroup_id': '456'}
        self.group = {'advertiser_id': '123', 'adgroup_id': '456', 'campaign_id': '789',
                      'promotion_type': 'NATIVE_SERIES', 'optimization_goal': 'VALUE', 'deep_bid_type': 'VO_MIN_ROAS',
                      'bid_type': 'BID_TYPE_NO_BID', 'vbo_window': 'ZERO_DAY', 'budget': 100,
                      'budget_mode': 'BUDGET_MODE_DYNAMIC_DAILY_BUDGET', 'targeting_optimization_mode': 'AUTOMATIC',
                      'placement_type': 'PLACEMENT_TYPE_NORMAL', 'placements': ['PLACEMENT_TIKTOK'],
                      'schedule_type': 'SCHEDULE_FROM_NOW', 'schedule_start_time': '2026-11-12 10:00:00',
                      'targeting_spec': {'location_ids': ['12345'], 'languages': ['en']}}
        self.campaign = {'advertiser_id': '123', 'campaign_id': '789', 'objective_type': 'APP_PROMOTION',
                         'app_promotion_type': 'MINIS', 'budget_optimize_on': False,
                         'budget_mode': 'BUDGET_MODE_INFINITE', 'smart_plus_adgroup_mode': 'MULTIPLE'}

    def validate(self, fields):
        value = {**self.patch, **fields}
        validate_update(value, now=NOW)
        validate_context(value, self.group, self.campaign)
        return value

    def test_required_ids_and_payload_types_fail_before_transport(self):
        transport = Mock()
        client = TikTokClient('fixture-token', transport=transport)
        for value in (None, [], {}, {'advertiser_id': '123'}, {**self.patch, 'adgroup_id': 456}, {**self.patch, 'advertiser_id': ' '}):
            with self.subTest(value=value), self.assertRaises(CliError):
                client.update_adgroup(value, smart_plus=True)
        transport.request.assert_not_called()

    def test_create_response_and_misnested_fields_are_rejected(self):
        for key in ('campaign_id', 'native_series_id', 'identity_type', 'deep_bid_type', 'budget_mode',
                    'pacing', 'operation_status', 'current_budget', 'location_ids', 'spc_audience_age'):
            with self.subTest(key=key), self.assertRaises(CliError):
                validate_update({**self.patch, key: 'value'}, now=NOW)

    def test_roas_boundaries_types_and_existing_bid_strategy(self):
        for value in (0.01, 1000):
            self.validate({'roas_bid': value})
        for value in (0, 0.009, 1000.01, True, '1.5', None, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(CliError):
                self.validate({'roas_bid': value})
        self.group['deep_bid_type'] = 'VO_HIGHEST_VALUE'
        with self.assertRaisesRegex(CliError, 'bid strategy'):
            self.validate({'roas_bid': 1.5})

    def test_bid_conditions_use_current_goal_and_effective_budget(self):
        with self.assertRaisesRegex(CliError, 'incompatible'):
            self.validate({'bid_price': 1})
        self.group.update(optimization_goal='CLICK', bid_type='BID_TYPE_CUSTOM')
        self.validate({'bid_price': 99})
        with self.assertRaisesRegex(CliError, 'lower'):
            self.validate({'bid_price': 100})
        self.campaign.update(budget_optimize_on=True, budget=200)
        self.validate({'bid_price': 199})
        self.group['optimization_goal'] = 'INSTALL'
        self.validate({'conversion_bid_price': 5})

    def test_budget_strategy_accepts_eligible_group_and_rejects_cbo(self):
        self.validate({'budget': 50, 'budget_auto_adjust_strategy': 'AUTO_BUDGET_INCREASE'})
        self.validate({'budget_auto_adjust_strategy': 'UNSET'})
        self.campaign['budget_optimize_on'] = True
        with self.assertRaisesRegex(CliError, 'CBO'):
            self.validate({'budget': 50})
        self.campaign['budget_optimize_on'] = False
        self.group['vbo_window'] = 'SEVEN_DAY'
        with self.assertRaisesRegex(CliError, 'eligible'):
            self.validate({'budget_auto_adjust_strategy': 'AUTO_BUDGET_INCREASE'})

    def test_schedule_patch_merges_for_validation_not_for_request(self):
        self.group.update(budget_mode='BUDGET_MODE_TOTAL', schedule_type='SCHEDULE_START_END',
                          schedule_start_time='2026-10-01 00:00:00', schedule_end_time='2026-12-01 00:00:00')
        self.validate({'schedule_end_time': '2026-12-02 00:00:00'})
        # Unchanged historical start times are not validated as newly supplied.
        with self.assertRaisesRegex(CliError, '12 hours'):
            self.validate({'schedule_start_time': '2026-10-01 00:00:00'})
        self.validate({'schedule_start_time': '2026-11-12 00:00:00'})
        with self.assertRaisesRegex(CliError, 'later than'):
            self.validate({'schedule_end_time': '2026-09-01 00:00:00'})
        self.group['budget_mode'] = 'BUDGET_MODE_DYNAMIC_DAILY_BUDGET'
        # Creation can already have a daily-budget START_END schedule. Updating
        # its end date must not be treated as explicitly setting schedule_type.
        self.validate({'schedule_end_time': '2026-12-02 00:00:00'})
        with self.assertRaisesRegex(CliError, 'lifetime'):
            self.validate({'schedule_type': 'SCHEDULE_START_END'})

    def test_schedule_absolute_limits_and_binary_dayparting(self):
        for field, value in (('schedule_start_time', '2028-01-01 00:00:01'),
                             ('schedule_end_time', '2038-01-01 00:00:01'),
                             ('schedule_start_time', '2026-11-12T12:00:00Z')):
            with self.subTest(field=field), self.assertRaises(CliError):
                validate_update({**self.patch, field: value}, now=NOW)
        for value in ('0' * 336, '1' * 336, '01' * 168):
            self.validate({'dayparting': value})
        for value in ('0' * 335, 'x' * 336, 0, None):
            with self.subTest(value=value), self.assertRaises(CliError):
                self.validate({'dayparting': value})

    def test_incremental_targeting_does_not_require_resupplying_locations(self):
        self.validate({'targeting_spec': {'languages': [], 'gender': 'GENDER_FEMALE'}})
        self.validate({'targeting_spec': {'spc_audience_age': 'OVER_TWENTY_FIVE'}})
        with self.assertRaisesRegex(CliError, '18 or 25'):
            self.validate({'targeting_spec': {'spc_audience_age': 'ALL'}})
        with self.assertRaisesRegex(CliError, 'AUTOMATIC'):
            self.validate({'targeting_optimization_mode': 'MANUAL'})

    def test_targeting_shapes_limits_and_null_relation(self):
        invalid = [{'location_ids': '123'}, {'zipcode_ids': ['x'] * 3001}, {'languages': [None]},
                   {'device_model_ids': ['x'], 'device_price_ranges': [0, 250]},
                   {'device_price_ranges': [0, 251]}, {'device_price_ranges': [50, 0]},
                   {'operating_systems': ['IOS', 'ANDROID']},
                   {'shopping_ads_retargeting_custom_audience_relation': None}]
        for target in invalid:
            with self.subTest(target=target), self.assertRaises(CliError):
                self.validate({'targeting_spec': target})

    def test_search_keywords_require_search_campaign_and_valid_pairs(self):
        fields = {'search_keywords': [{'keyword': 'drama series', 'match_type': 'PHRASE_WORD'}]}
        with self.assertRaisesRegex(CliError, 'Search campaign'):
            self.validate(fields)
        self.campaign['is_search_campaign'] = True
        self.validate(fields)
        for values in ([], [{}], [{'keyword': 'a-b', 'match_type': 'PHRASE_WORD'}],
                       [{'keyword': 'x' * 81, 'match_type': 'PRECISE_WORD'}],
                       fields['search_keywords'] * 1001):
            with self.subTest(values=values[:1]), self.assertRaises(CliError):
                self.validate({'search_keywords': values})

    def test_rta_categories_and_suggestions(self):
        self.validate({'suggestion_audience_enabled': True})
        self.campaign['rta_id'] = 'rta'
        with self.assertRaisesRegex(CliError, 'RTA'):
            self.validate({'suggestion_audience_enabled': True})
        with self.assertRaisesRegex(CliError, 'RTA'):
            self.validate({'targeting_spec': {'excluded_audience_ids': ['a']}})
        self.campaign['special_industries'] = ['HOUSING']
        with self.assertRaisesRegex(CliError, 'special_industries'):
            self.validate({'value_rule_set_id': 'rule'})

    def test_catalog_retargeting_conditions_use_merged_state(self):
        self.group.update(promotion_type='WEBSITE', targeting_optimization_mode='MANUAL')
        self.campaign.update(objective_type='WEB_CONVERSION', sales_destination='WEBSITE', catalog_type='ECOMMERCE')
        self.validate({'targeting_spec': {'shopping_ads_retargeting_type': 'LAB1', 'shopping_ads_retargeting_actions_days': 7}})
        with self.assertRaisesRegex(CliError, 'LAB1/LAB2'):
            self.validate({'targeting_spec': {'shopping_ads_retargeting_type': 'LAB1'}})
        self.validate({'targeting_spec': {'shopping_ads_retargeting_type': 'LAB3',
            'included_custom_actions': [{'code': 'PURCHASE', 'days': 180}]}})
        with self.assertRaisesRegex(CliError, 'days'):
            self.validate({'targeting_spec': {'included_custom_actions': [{'code': 'PURCHASE', 'days': 181}]}})

    def test_targeting_placement_and_saved_audience_conditions(self):
        self.validate({'organic_comment_disabled': False})
        self.validate({'targeting_spec': {'saved_audience_id': 'saved'}})
        self.campaign['special_industries'] = ['HOUSING']
        with self.assertRaisesRegex(CliError, 'Saved audiences'):
            self.validate({'targeting_spec': {'saved_audience_id': 'saved'}})
        self.group['placements'] = ['PLACEMENT_GLOBAL_APP_BUNDLE']
        for fields in ({'organic_comment_disabled': False},
                       {'targeting_spec': {'purchase_intention_keyword_ids': ['intent']}},
                       {'targeting_spec': {'isp_ids': ['isp']}},
                       {'targeting_spec': {'actions': [{'action_scene': 'VIDEO_RELATED', 'action_category_ids': ['category']}]}}):
            with self.subTest(fields=fields), self.assertRaises(CliError):
                self.validate(fields)
        self.group['promotion_type'] = 'WEBSITE'
        self.group['targeting_optimization_mode'] = 'MANUAL'
        with self.assertRaisesRegex(CliError, 'AUTOMATIC'):
            self.validate({'suggestion_audience_enabled': False})

    def test_invalid_remote_budget_state_fails_closed(self):
        self.campaign['budget_optimize_on'] = None
        with self.assertRaisesRegex(CliError, 'budget strategy'):
            self.validate({'budget': 100})
        self.campaign.update(budget_optimize_on=True, budget=float('nan'))
        self.group.update(optimization_goal='CLICK', bid_type='BID_TYPE_CUSTOM')
        with self.assertRaisesRegex(CliError, 'lower'):
            self.validate({'bid_price': 10})

    def test_http_sends_only_patch_after_reading_conditional_state(self):
        transport = Mock()
        def send(endpoint, **kwargs):
            if endpoint.path == 'smart_plus/adgroup/get/':
                return {'code': 0, 'data': {'list': [self.group]}}
            if endpoint.path == 'smart_plus/campaign/get/':
                return {'code': 0, 'data': {'list': [self.campaign]}}
            return {'code': 0, 'data': {'adgroup_id': '456'}}
        transport.request.side_effect = send
        client = TikTokClient('fixture-token', transport=transport)
        value = {**self.patch, 'roas_bid': 1.5, 'future': {'false': False, 'clear': [], 'null': None}}
        before = copy.deepcopy(value)
        client.update_adgroup(value, smart_plus=True)
        self.assertEqual(value, before)
        self.assertEqual(transport.request.call_args.kwargs['body'], before)
        self.assertEqual([call.args[0].method for call in transport.request.call_args_list], ['GET', 'GET', 'POST'])
        self.assertNotIn('bid_price', transport.request.call_args.kwargs['body'])

    def test_invalid_context_or_read_failure_never_reaches_write(self):
        transport = Mock()
        client = TikTokClient('fixture-token', transport=transport)
        with patch.object(client, 'get_adgroup', side_effect=CliError('read failed')):
            with self.assertRaisesRegex(CliError, 'read failed'):
                client.update_adgroup({**self.patch, 'roas_bid': 1}, smart_plus=True)
        self.campaign['budget_optimize_on'] = True
        with patch.object(client, 'get_adgroup', return_value=self.group), patch.object(client, 'get_campaign', return_value=self.campaign):
            with self.assertRaisesRegex(CliError, 'CBO'):
                client.update_adgroup({**self.patch, 'budget': 50}, smart_plus=True)
        transport.request.assert_not_called()

    def test_simple_rename_unknown_fields_and_false_flags_need_no_read(self):
        transport = Mock()
        transport.request.return_value = {'code': 0}
        value = {**self.patch, 'adgroup_name': 'rename', 'comment_disabled': False, 'share_disabled': False,
                 'future': {'zero': 0, 'empty': [], 'null': None}}
        TikTokClient('fixture-token', transport=transport).update_adgroup(value, smart_plus=True)
        transport.request.assert_called_once()
        self.assertEqual(transport.request.call_args.kwargs['body'], value)

    def test_reviewed_official_incremental_example_sends_exact_body(self):
        value = json.loads((Path(__file__).parent / 'fixtures/tiktok_smartplus_20261112.json').read_text())['requests']['update']
        transport = Mock()
        transport.request.return_value = {'code': 0}
        TikTokClient('fixture-token', transport=transport).update_adgroup(value, smart_plus=True)
        transport.request.assert_called_once()
        self.assertEqual(transport.request.call_args.args[0].path, 'smart_plus/adgroup/update/')
        self.assertEqual(transport.request.call_args.kwargs['body'], value)

    def test_cli_supports_explicit_false_and_new_update_fields(self):
        args = build_parser().parse_args(['tiktok', 'smartplus-adgroups', 'update', '456', '--account-id', '123',
            '--no-comment-disabled', '--no-share-disabled', '--no-organic-comment-disabled',
            '--no-suggestion-audience-enabled', '--budget-auto-adjust-strategy', 'UNSET'])
        value = commands.build_adgroup_update_payload(args)
        for key in ('comment_disabled', 'share_disabled', 'organic_comment_disabled', 'suggestion_audience_enabled'):
            self.assertIs(value[key], False)
        self.assertEqual(value['budget_auto_adjust_strategy'], 'UNSET')
        self.assertNotIn('pacing', value)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(['tiktok', 'smartplus-adgroups', 'update', '456', '--pacing', 'PACING_MODE_SMOOTH'])

    def test_cli_surfaces_bid_propagation_without_changing_response(self):
        args = build_parser().parse_args(['tiktok', 'smartplus-adgroups', 'update', '456', '--account-id', '123', '--roas-bid', '1.5'])
        client = Mock()
        client.update_adgroup.return_value = {'code': 0, 'data': {'adgroup_id': '456'}}
        with patch.object(commands, 'resolve_tiktok_client', return_value=('123', client)), \
             patch.object(commands, 'print_output') as output, redirect_stderr(io.StringIO()) as stderr:
            commands.command_tiktok_adgroups_update(args)
        self.assertIn('sibling', stderr.getvalue())
        self.assertEqual(output.call_args.args[0], client.update_adgroup.return_value)
        self.assertEqual(client.update_adgroup.call_args.args[0], {**self.patch, 'roas_bid': 1.5})


if __name__ == '__main__':
    unittest.main()
