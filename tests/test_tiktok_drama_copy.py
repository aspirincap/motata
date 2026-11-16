"""MINIS copies must retain Drama budgeting instead of ordinary iOS defaults."""
import unittest

from motata_cli.tiktok import commands


class DramaCopyTests(unittest.TestCase):
    def build(self, *, cbo):
        campaign = {'campaign_id': 'source', 'objective_type': 'APP_PROMOTION', 'app_promotion_type': 'MINIS',
                    'campaign_type': 'REGULAR_CAMPAIGN', 'budget_optimize_on': cbo,
                    'budget_mode': 'BUDGET_MODE_DYNAMIC_DAILY_BUDGET' if cbo else 'BUDGET_MODE_INFINITE',
                    'budget': 50 if cbo else 0}
        group = {'adgroup_id': 'source-group', 'promotion_type': 'NATIVE_SERIES', 'native_series_id': 'series',
                 'identity_type': 'TT_USER', 'identity_id': 'identity', 'budget': 20,
                 'budget_mode': 'BUDGET_MODE_DYNAMIC_DAILY_BUDGET', 'deep_bid_type': 'VO_MIN_ROAS',
                 'roas_bid': 1.5, 'vbo_window': 'ZERO_DAY', 'bid_price': 0, 'conversion_bid_price': 0,
                 'optimization_event': 'IMPRESSION_LEVEL_AD_REVENUE',
                 'targeting_spec': {'location_ids': ['12345']}}
        strategy = commands.infer_campaign_copy_strategy(campaign, [group], [], smart_plus=True)
        copied_campaign = commands.build_campaign_copy_payload(campaign, advertiser_id='123', campaign_name='copy',
                                                                operation_status='DISABLE', copy_strategy=strategy)
        group_strategy = commands.infer_adgroup_copy_strategy(group, campaign_strategy=strategy)
        copied_group = commands.build_adgroup_copy_payload(group, advertiser_id='123', campaign_id='new',
                                                           operation_status='DISABLE', adgroup_strategy=group_strategy)
        return copied_campaign, copied_group

    def test_adgroup_budget_and_targeting_survive_minis_copy(self):
        campaign, group = self.build(cbo=False)
        self.assertEqual(campaign['budget_mode'], 'BUDGET_MODE_INFINITE')
        self.assertNotIn('budget', campaign)
        self.assertNotIn('is_advanced_dedicated_campaign', campaign)
        self.assertEqual(campaign['campaign_type'], 'REGULAR_CAMPAIGN')
        self.assertEqual(group['budget'], 20)
        self.assertEqual(group['targeting_spec'], {'location_ids': ['12345']})
        self.assertNotIn('location_ids', group)
        self.assertEqual(group['deep_bid_type'], 'VO_MIN_ROAS')
        self.assertEqual(group['optimization_event'], 'AD_REVENUE_VALUE')
        self.assertNotIn('bid_price', group)

    def test_cbo_copy_omits_response_only_adgroup_budget(self):
        campaign, group = self.build(cbo=True)
        self.assertEqual(campaign['budget'], 50)
        self.assertNotIn('budget', group)
        self.assertNotIn('budget_mode', group)

    def test_flat_targeting_is_nested_only_for_smartplus_copy(self):
        source = {'adgroup_name': 'source', 'promotion_type': 'NATIVE_SERIES',
                  'location_ids': ['12345'], 'languages': ['en'], 'operating_systems': ['ANDROID'],
                  'spc_audience_age': 'OVER_TWENTY_FIVE', 'dayparting': '1' * 336,
                  'tiktok_subplacements': ['PINE_DRAMA']}
        for smart_plus in (False, True):
            with self.subTest(smart_plus=smart_plus):
                result = commands.build_adgroup_copy_payload(source, advertiser_id='123', campaign_id='new',
                    operation_status='DISABLE', adgroup_strategy={'target_smart_plus': smart_plus})
                if smart_plus:
                    self.assertEqual(result['targeting_spec'], {'location_ids': ['12345'], 'languages': ['en'],
                        'operating_systems': ['ANDROID'], 'spc_audience_age': 'OVER_TWENTY_FIVE'})
                    self.assertNotIn('location_ids', result)
                    self.assertNotIn('operating_systems', result)
                    self.assertEqual(result['dayparting'], '1' * 336)
                    self.assertEqual(result['tiktok_subplacements'], ['PINE_DRAMA'])
                else:
                    self.assertEqual(result['location_ids'], ['12345'])
                    self.assertNotIn('targeting_spec', result)


if __name__ == '__main__':
    unittest.main()
