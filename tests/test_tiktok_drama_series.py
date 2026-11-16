from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from motata_cli.tiktok.operations import OperationStore
from motata_cli.tiktok.errors import TikTokError
from motata_cli.tiktok import drama_rules
from motata_cli.tiktok.services import drama_series as service


def payload():
    return {"advertiser_id": "123", "campaign": {"campaign_name": "drama-test", "budget": 50,
                                                  "budget_mode": drama_rules.DAILY},
            "adgroup": {"adgroup_name": "group-test", "identity_type": "TT_USER", "identity_id": "identity",
                        "native_series_id": "series", "targeting_spec": {"location_ids": ["12345"]},
                        "deep_bid_type": "VO_HIGHEST_VALUE", "schedule_type": "SCHEDULE_FROM_NOW",
                        "schedule_start_time": "2026-12-01 00:00:00"},
            "ad": {"ad_name": "ad-test", "ad_configuration": {"call_to_action_id": "cta"},
                   "creative_list": [{"creative_info": {"ad_format": "SINGLE_VIDEO", "video_info": {"video_id": "video"}}}]}}


class DramaSeriesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = OperationStore(Path(self.directory.name) / "state.db")
        self.plan = service.build_plan(payload())
        self.client = Mock()
        self.client.get_account_info.return_value = {"code": 0, "data": {"list": [{"advertiser_id": "123"}]}}
        self.client.list_identities.return_value = {"code": 0, "data": {"identity_list": [{"identity_id": "identity"}]}}
        self.client.list_native_series.return_value = {"code": 0, "data": {"native_series_list": [{"native_series_id": "series"}]}}
        for level, object_id in (("campaign", "c"), ("adgroup", "g"), ("ad", "a")):
            getattr(self.client, "create_" + level).return_value = {"code": 0, "data": {level + "_id": object_id}}
            getattr(self.client, "get_" + level).return_value = {**self.plan[level], level + "_id": object_id}
        self.client.get_adgroup.return_value['campaign_id'] = 'c'
        self.client.get_ad.return_value['adgroup_id'] = 'g'

    def test_plan_is_local_preserves_new_fields_and_disables_all_levels(self):
        value = payload()
        value["campaign"]["operation_status"] = "ENABLE"
        value["adgroup"]["future_field"] = {"empty": [], "null": None}
        plan = service.build_plan(value)
        self.assertEqual(value["campaign"]["operation_status"], "ENABLE")
        for level in ("campaign", "adgroup", "ad"):
            self.assertEqual(plan[level]["operation_status"], "DISABLE")
        self.assertEqual(plan["adgroup"]["future_field"], value["adgroup"]["future_field"])
        self.assertEqual(plan["campaign"]["app_promotion_type"], "MINIS")

    def test_reviewed_official_creation_examples_preserve_request_fields(self):
        requests = json.loads((Path(__file__).parent / 'fixtures/tiktok_smartplus_20261112.json').read_text())['requests']
        value = {level: copy.deepcopy(requests[level]) for level in ('campaign', 'adgroup', 'ad')}
        value['advertiser_id'] = value['campaign']['advertiser_id']
        del value['adgroup']['campaign_id']
        del value['ad']['adgroup_id']
        plan = service.build_plan(value)
        for level in ('campaign', 'adgroup', 'ad'):
            for key, expected in value[level].items():
                self.assertEqual(plan[level][key], expected, (level, key))
            self.assertEqual(plan[level]['operation_status'], 'DISABLE')
        # The official example uses daily CBO and a START_END schedule.
        self.assertEqual(plan['campaign']['budget_mode'], drama_rules.DAILY)
        self.assertEqual(plan['adgroup']['schedule_type'], 'SCHEDULE_START_END')

    def test_distribution_and_mutual_exclusion(self):
        value = payload()
        value["adgroup"]["drama_selling_setting"] = {"distribution_link": "https://example.test/series"}
        with self.assertRaisesRegex(Exception, "exactly one"):
            service.build_plan(value)
        del value["adgroup"]["native_series_id"]
        self.assertEqual(service.validate_local(service.build_plan(value))["mode"], "distribution")

    def test_distribution_execute_and_resume_skip_owned_series_lookup(self):
        value = payload()
        del value['adgroup']['native_series_id']
        value['adgroup']['drama_selling_setting'] = {'distribution_link': 'https://example.test/series'}
        plan = service.build_plan(value)
        for level in ('campaign', 'adgroup', 'ad'):
            getattr(self.client, 'get_' + level).return_value.update(plan[level])
        self.assertEqual(service.execute(plan, self.client, store=self.store)['status'], 'success')
        self.assertEqual(service.execute(plan, self.client, store=self.store)['status'], 'success')
        self.client.list_native_series.assert_not_called()
        self.client.create_adgroup.assert_called_once()

    def test_invalid_budget_link_and_nested_identity_fail_locally(self):
        for budget in (float('nan'), float('inf'), -1, True):
            value = payload()
            value['campaign']['budget'] = budget
            with self.assertRaisesRegex(Exception, 'Budget'):
                service.build_plan(value)
        value = payload()
        value['ad']['creative_list'][0]['creative_info'].update(identity_type='TT_USER', identity_id='')
        with self.assertRaisesRegex(Exception, 'identity_id'):
            service.build_plan(value)
        value = payload()
        del value['adgroup']['native_series_id']
        value['adgroup']['drama_selling_setting'] = {'distribution_link': 'https://'}
        with self.assertRaisesRegex(Exception, r'HTTP\(S\) URL'):
            service.build_plan(value)

    def test_auto_ad_name_is_optional_and_resume_reuses_generated_name(self):
        for explicit in (False, True):
            value = payload()
            value['ad'].pop('ad_name')
            if explicit:
                value['ad']['ad_name'] = ''
            plan = service.build_plan(value)
            self.client.get_ad.return_value['ad_name'] = 'platform generated name'
            first = service.execute(plan, self.client, store=self.store)
            self.assertEqual(first['status'], 'success')
            self.assertEqual(first['resolved_names']['ad'], 'platform generated name')
            calls = self.client.create_ad.call_count
            self.assertEqual(service.execute(plan, self.client, store=self.store)['status'], 'success')
            self.assertEqual(self.client.create_ad.call_count, calls)
            self.assertEqual(self.client.create_ad.call_args.args[0]['ad_name'], '')

    def test_autonamed_reconciliation_requires_matching_assets_and_cta(self):
        value = payload()
        value['ad']['ad_name'] = ''
        plan = service.build_plan(value)
        self.client.create_ad.side_effect = TikTokError('timeout', write_outcome='unknown')
        service.execute(plan, self.client, store=self.store)
        self.client.get_ad.return_value['ad_name'] = 'autogenerated'
        self.client.get_ad.return_value['creative_list'] = copy.deepcopy(plan['ad']['creative_list'])
        self.client.get_ad.return_value['creative_list'][0]['creative_info']['video_info']['video_id'] = 'wrong'
        with self.assertRaisesRegex(Exception, 'assets/identity/CTA'):
            service.reconcile(plan, self.client, job_id=plan['plan_id'], step='ad', object_id='a', store=self.store)
        self.client.get_ad.return_value['creative_list'] = copy.deepcopy(plan['ad']['creative_list'])
        service.reconcile(plan, self.client, job_id=plan['plan_id'], step='ad', object_id='a', store=self.store)
        self.assertEqual(service.execute(plan, self.client, store=self.store)['status'], 'success')
        self.client.create_ad.assert_called_once()

    def test_creatives_may_use_a_different_authorized_identity(self):
        value = payload()
        value['ad']['creative_list'][0]['creative_info'].update(
            identity_type='BC_AUTH_TT', identity_id='creative-identity', identity_authorized_bc_id='bc')
        plan = service.build_plan(value)
        def identities(account, **options):
            if options['identity_type'] == 'BC_AUTH_TT':
                self.assertEqual(options['identity_authorized_bc_id'], 'bc')
                return {'data': {'identity_list': [{'identity_id': 'creative-identity', 'identity_type': 'BC_AUTH_TT', 'identity_authorized_bc_id': 'bc'}]}}
            return {'data': {'identity_list': [{'identity_id': 'identity', 'identity_type': 'TT_USER'}]}}
        self.client.list_identities.side_effect = identities
        self.assertEqual(service.validate_remote(plan, self.client)['creative_identity_access'], 'verified')
        self.assertEqual(self.client.list_identities.call_count, 2)
        self.client.list_identities.side_effect = None
        self.client.list_identities.return_value = {'data': {'identity_list': []}}
        self.assertEqual(service.execute(plan, self.client, store=self.store)['status'], 'failed')
        self.client.create_campaign.assert_not_called()

    def test_explicit_null_or_empty_distribution_branch_is_not_ignored(self):
        for invalid in (None, {}, {'distribution_link': ''}):
            value = payload()
            value['adgroup']['drama_selling_setting'] = invalid
            with self.assertRaisesRegex(Exception, 'exactly one'):
                service.build_plan(value)
            del value['adgroup']['native_series_id']
            with self.assertRaisesRegex(Exception, r'HTTP\(S\) URL'):
                service.build_plan(value)

    def test_age_subplacements_and_creative_count_boundaries(self):
        value = payload()
        value['adgroup']['targeting_spec']['spc_audience_age'] = 'OVER_TWENTY_FIVE'
        value['adgroup']['tiktok_subplacements'] = ['PINE_DRAMA']
        value['ad']['creative_list'] *= 50
        service.build_plan(value)
        value['ad']['creative_list'].append(copy.deepcopy(value['ad']['creative_list'][0]))
        with self.assertRaisesRegex(Exception, '50'):
            service.build_plan(value)
        value = payload()
        value['adgroup']['targeting_spec']['spc_audience_age'] = 'ALL'
        with self.assertRaisesRegex(Exception, '18 or 25'):
            service.build_plan(value)
        value = payload()
        value['ad']['creative_list'] = None
        with self.assertRaisesRegex(Exception, 'creative_list'):
            service.build_plan(value)

    def test_creation_readback_checks_account_and_parent(self):
        self.client.get_adgroup.return_value['campaign_id'] = 'other'
        result = service.execute(self.plan, self.client, store=self.store)
        self.assertIn('parent', result['error'])
        self.client.create_ad.assert_not_called()
        self.client.get_adgroup.return_value['campaign_id'] = 'c'
        self.client.get_adgroup.return_value['advertiser_id'] = 'other-account'
        result = service.execute(self.plan, self.client, store=self.store)
        self.assertIn('advertiser', result['error'])
        self.client.create_ad.assert_not_called()

    def test_missing_discovery_collection_is_not_empty_success(self):
        self.client.list_identities.return_value = {'data': {}}
        result = service.execute(self.plan, self.client, store=self.store)
        self.assertIn('collection', result['error'])
        self.client.create_campaign.assert_not_called()

    def test_list_shaped_discovery_pages_and_empty_results(self):
        self.client.get_account_info.return_value = {'data': [{'advertiser_id': '123'}]}
        self.client.list_identities.side_effect = [
            {'data': [{'identity_id': 'unrelated'}]}, {'data': [{'identity_id': 'identity'}]}]
        self.client.list_native_series.side_effect = [
            {'data': [{'native_series_id': 'unrelated'}]}, {'data': [{'native_series_id': 'series'}]}]
        self.assertTrue(service.validate_remote(self.plan, self.client)['valid'])
        self.assertEqual(service._rows({'data': []}), [])
        self.client.list_identities.side_effect = None
        self.client.list_identities.return_value = {'data': []}
        with self.assertRaisesRegex(Exception, 'not found'):
            service.validate_remote(self.plan, self.client)

    def test_distribution_http_is_not_rejected_as_https_only(self):
        value = payload()
        del value['adgroup']['native_series_id']
        value['adgroup']['drama_selling_setting'] = {'distribution_link': 'http://example.test/series'}
        plan = service.build_plan(value)
        self.assertEqual(plan['adgroup']['drama_selling_setting'], value['adgroup']['drama_selling_setting'])

    def test_shared_strategy_includes_non_cbo_and_numeric_equality(self):
        a = {**self.plan['adgroup'], 'roas_bid': 1}
        b = {**a, 'roas_bid': 1.0}
        drama_rules.validate_shared_settings(self.plan['campaign'], [a, b])
        b['deep_bid_type'] = 'VO_MIN_ROAS'
        with self.assertRaisesRegex(Exception, 'deep_bid_type'):
            drama_rules.validate_shared_settings({'budget_optimize_on': False}, [a, b])

    def test_repeated_discovery_page_fails_before_creation(self):
        self.client.list_identities.return_value['data']['identity_list'] = [{'identity_id': 'unrelated'}]
        result = service.execute(self.plan, self.client, store=self.store)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('pagination repeated', result['error'])
        self.assertEqual(self.client.list_identities.call_count, 2)
        self.client.create_campaign.assert_not_called()

    def test_cbo_and_adgroup_budget_require_different_fields(self):
        value = payload()
        value['adgroup']['budget'] = 20
        with self.assertRaisesRegex(Exception, 'CBO'):
            service.build_plan(value)
        value['campaign'].update(budget_optimize_on=False, budget_mode=drama_rules.INFINITE)
        del value['campaign']['budget']
        value['adgroup']['budget_mode'] = drama_rules.DAILY
        self.assertTrue(service.validate_local(service.build_plan(value))['valid'])

    def test_target_roas_requires_daily_budget_and_positive_bounded_target(self):
        value = payload()
        value['adgroup'].update(deep_bid_type='VO_MIN_ROAS', roas_bid=1.5)
        service.build_plan(value)
        for roas in (None, 0, 0.001, 1001):
            value['adgroup']['roas_bid'] = roas
            with self.assertRaisesRegex(Exception, 'roas_bid'):
                service.build_plan(value)
        value['adgroup']['roas_bid'] = 1.5
        value['campaign']['budget_mode'] = drama_rules.TOTAL
        with self.assertRaisesRegex(Exception, 'daily'):
            service.build_plan(value)

    def test_shared_cbo_identity_and_roas_settings(self):
        a = self.plan['adgroup']
        for field, value in (('identity_id', 'other'), ('native_series_id', 'other'),
                             ('deep_bid_type', 'VO_MIN_ROAS'), ('bid_type', 'other')):
            b = {**a, field: value}
            with self.assertRaisesRegex(Exception, field):
                drama_rules.validate_shared_settings(self.plan['campaign'], [a, b])

    def test_target_roas_active_campaign_conflict_fails_before_write(self):
        value = payload()
        value['adgroup'].update(deep_bid_type='VO_MIN_ROAS', roas_bid=1.5)
        plan = service.build_plan(value)
        self.client.list_adgroups.return_value = {'code': 0, 'data': {
            'list': [{'native_series_id': 'series', 'campaign_id': 'active', 'targeting_spec': {'location_ids': ['12345']}}],
            'page_info': {'total_page': 1}}}
        self.client.get_campaign.return_value['operation_status'] = 'ENABLE'
        result = service.execute(plan, self.client, store=self.store)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Target ROAS conflict', result['error'])
        self.client.create_campaign.assert_not_called()
        self.client.get_campaign.return_value['operation_status'] = 'DISABLE'
        self.assertIn('no_exact_id_conflict', service.validate_remote(plan, self.client)['target_roas_conflicts'])

    def test_target_roas_resume_excludes_its_own_saved_campaign(self):
        value = payload()
        value['adgroup'].update(deep_bid_type='VO_MIN_ROAS', roas_bid=1.5)
        plan = service.build_plan(value)
        self.client.list_adgroups.return_value = {'code': 0, 'data': {'list': [], 'page_info': {'total_page': 1}}}
        self.client.get_adgroup.return_value.update(plan['adgroup'])
        self.assertEqual(service.execute(plan, self.client, store=self.store)['status'], 'success')
        self.client.list_adgroups.return_value['data']['list'] = [{**plan['adgroup'], 'campaign_id': 'c'}]
        self.assertEqual(service.execute(plan, self.client, store=self.store)['status'], 'success')
        self.client.create_campaign.assert_called_once()

    def test_creative_cta_and_nested_targeting_are_required(self):
        for transform in ('cta', 'media', 'target'):
            value = payload()
            if transform == 'cta':
                value['ad']['ad_configuration'] = {}
            elif transform == 'media':
                value['ad']['creative_list'][0]['creative_info'].pop('video_info')
            else:
                value['adgroup']['location_ids'] = ['12345']
            with self.assertRaises(Exception):
                service.build_plan(value)

    def test_concurrent_job_is_rejected_and_crashed_claim_is_not_replayed(self):
        with self.store.lock('crash'):
            with self.assertRaisesRegex(Exception, 'already running'):
                with OperationStore(self.store.path).lock('crash'):
                    self.fail('A second process must not acquire this job')
            self.store.bind('crash', self.plan)
            with self.assertRaises(KeyboardInterrupt):
                self.store.step('crash', 'campaign', self.plan['campaign'], Mock(side_effect=KeyboardInterrupt), ('campaign_id',))
        submit = Mock()
        with self.store.lock('crash'):
            self.assertEqual(self.store.rows('crash')[0]['state'], 'unknown')
            with self.assertRaisesRegex(Exception, 'unknown'):
                self.store.step('crash', 'campaign', self.plan['campaign'], submit, ('campaign_id',), retry_failed=True)
        submit.assert_not_called()

    def test_changed_identity_status_or_parent_is_rejected(self):
        for edit in ({"operation_status": "ENABLE"}, {"identity_type": "OTHER"},
                     {"identity_type": "BC_AUTH_TT"}, {"campaign_id": "other"}):
            plan = copy.deepcopy(self.plan)
            plan["adgroup"].update(edit)
            with self.assertRaises(Exception):
                service.validate_local(plan)

    def test_create_and_resume_do_not_duplicate_successful_steps(self):
        result = service.execute(self.plan, self.client, store=self.store)
        self.assertEqual(result["status"], "success")
        again = service.execute(self.plan, self.client, store=self.store)
        self.assertEqual(again["objects"], result["objects"])
        for level in ("campaign", "adgroup", "ad"):
            getattr(self.client, "create_" + level).assert_called_once()
        body = self.client.create_adgroup.call_args.args[0]
        self.assertEqual(body["campaign_id"], "c")
        self.assertTrue(0 < int(body["request_id"]) < 2**63)

    def test_unknown_write_stops_and_cannot_replay(self):
        self.client.create_adgroup.side_effect = TikTokError("timeout", write_outcome="unknown")
        result = service.execute(self.plan, self.client, store=self.store)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(result["steps"][-1]["state"], "unknown")
        service.execute(self.plan, self.client, store=self.store, retry_failed=True)
        self.client.create_campaign.assert_called_once()
        self.client.create_adgroup.assert_called_once()
        self.client.create_ad.assert_not_called()

    def test_explicit_resume_can_retry_confirmed_rejection_only(self):
        self.client.create_adgroup.side_effect = TikTokError("rejected", write_outcome="failed")
        service.execute(self.plan, self.client, store=self.store)
        self.client.create_adgroup.side_effect = None
        result = service.execute(self.plan, self.client, store=self.store, retry_failed=True)
        self.assertEqual(result["status"], "success")
        self.client.create_campaign.assert_called_once()
        self.assertEqual(self.client.create_adgroup.call_count, 2)

    def test_unknown_without_id_can_only_reconcile_verified_object(self):
        self.client.create_adgroup.return_value = {"code": 0, "data": {}}
        service.execute(self.plan, self.client, store=self.store)
        self.client.get_adgroup.return_value["campaign_id"] = "other-parent"
        with self.assertRaisesRegex(Exception, "parent"):
            service.reconcile(self.plan, self.client, job_id=self.plan["plan_id"], step="adgroup", object_id="g", store=self.store)
        self.client.get_adgroup.return_value["campaign_id"] = "c"
        service.reconcile(self.plan, self.client, job_id=self.plan["plan_id"], step="adgroup", object_id="g", store=self.store)
        result = service.execute(self.plan, self.client, store=self.store)
        self.assertEqual(result["status"], "success")
        self.client.create_adgroup.assert_called_once()

    def test_plan_changed_under_same_job_is_refused(self):
        service.execute(self.plan, self.client, store=self.store)
        changed = copy.deepcopy(self.plan)
        changed["campaign"]["budget"] = 100
        with self.assertRaisesRegex(Exception, "different TikTok plan"):
            service.execute(changed, self.client, store=self.store)

    def test_missing_access_fails_before_any_create(self):
        self.client.list_identities.return_value = {"code": 0, "data": {"identity_list": []}}
        result = service.execute(self.plan, self.client, store=self.store)
        self.assertEqual(result["status"], "failed")
        self.client.create_campaign.assert_not_called()


if __name__ == "__main__":
    unittest.main()
