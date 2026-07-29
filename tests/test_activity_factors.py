from __future__ import annotations

import unittest

from motata_cli.report.activity_factors import build_activity_factor_report, rank_activity_targets


class ActivityFactorTests(unittest.TestCase):
    def test_meta_status_activity_becomes_factor_with_kpi_snapshot(self) -> None:
        activities = {
            "rows": [
                {
                    "object_id": "cmp-1",
                    "object_type": "campaign",
                    "object_name": "Scale Campaign",
                    "event_type": "campaign_status_update",
                    "translated_event_type": "Campaign status update",
                    "date_time_in_timezone": "2026-05-14T10:00:00+0800",
                    "actor_name": "Ada",
                },
                {
                    "object_id": "cmp-1",
                    "object_type": "campaign",
                    "object_name": "Scale Campaign",
                    "event_type": "campaign_status_update",
                    "translated_event_type": "Campaign status update",
                    "date_time_in_timezone": "2026-05-14T12:00:00+0800",
                    "actor_name": "Ada",
                },
            ]
        }
        current_rows = {
            "campaign": [
                {
                    "campaign_id": "cmp-1",
                    "campaign_name": "Scale Campaign",
                    "spend": "100",
                    "actions": [{"action_type": "purchase", "value": "10"}],
                    "action_values": [{"action_type": "purchase", "value": "350"}],
                }
            ]
        }

        result = build_activity_factor_report("meta", activities, current_rows)

        self.assertEqual(result["summary"]["factor_count"], 1)
        factor = result["factors"][0]
        self.assertEqual(factor["level"], "campaign")
        self.assertEqual(factor["object_id"], "cmp-1")
        self.assertEqual(factor["activity_count"], 2)
        self.assertEqual(factor["current"]["spend"], 100)
        self.assertEqual(factor["current"]["revenue"], 350)
        self.assertAlmostEqual(factor["current"]["roas"], 3.5)
        self.assertIn("状态", factor["implication"])

    def test_tiktok_budget_activity_targets_adgroup_and_matches_report_row(self) -> None:
        rows = [
            {
                "Time": "2026-05-14 01:15",
                "log_object_type": "Ad group",
                "Object ID": "ag-1",
                "Object": "Budget",
                "Operator": "Grace",
                "Activity details": '[{"action":"Change","name":"Budget","before_after":[{"before":"100","after":"150"}]}]',
            }
        ]
        targets = rank_activity_targets("tiktok", rows)
        current_rows = {
            "adgroup": [
                {
                    "dimensions": {"adgroup_id": "ag-1"},
                    "metrics": {
                        "adgroup_name": "Adgroup One",
                        "spend": "150",
                        "conversion": "12",
                        "total_purchase_value": "450",
                    },
                }
            ]
        }

        result = build_activity_factor_report("tiktok", {"rows": rows}, current_rows)

        self.assertEqual(targets[0]["level"], "adgroup")
        self.assertEqual(result["factors"][0]["object_name"], "Adgroup One")
        self.assertEqual(result["factors"][0]["current"]["result"], 12)
        self.assertEqual(result["factors"][0]["current"]["roas"], 3.0)
        self.assertIn("预算", result["factors"][0]["implication"])

    def test_meta_adgroup_activity_maps_to_adset_level(self) -> None:
        rows = [
            {
                "object_id": "adset-1",
                "object_type": "ADGROUP",
                "object_name": "Meta Ad Set One",
                "event_type": "update_ad_run_status",
                "translated_event_type": "广告状态更新",
                "date_time_in_timezone": "2026/5/12 18:16",
            }
        ]
        current_rows = {
            "adset": [
                {
                    "adset_id": "adset-1",
                    "adset_name": "Meta Ad Set One",
                    "spend": "120",
                    "actions": [{"action_type": "purchase", "value": "4"}],
                    "action_values": [{"action_type": "purchase", "value": "240"}],
                }
            ]
        }

        targets = rank_activity_targets("meta", rows)
        result = build_activity_factor_report("meta", {"rows": rows}, current_rows)

        self.assertEqual(targets[0]["level"], "adset")
        self.assertEqual(result["factors"][0]["level"], "adset")
        self.assertEqual(result["factors"][0]["object_name"], "Meta Ad Set One")
        self.assertEqual(result["factors"][0]["current"]["spend"], 120)
        self.assertEqual(result["factors"][0]["current"]["roas"], 2.0)

    def test_targeted_tiktok_activities_generate_concise_conclusions(self) -> None:
        rows = [
            {
                "Time": "2026-05-14 01:15",
                "log_object_type": "Campaign",
                "Object ID": "cmp-1",
                "Object": "Status",
                "Operator": "Grace",
            },
            {
                "Time": "2026-05-14 03:15",
                "log_object_type": "Campaign",
                "Object ID": "cmp-1",
                "Object": "Status",
                "Operator": "Grace",
            }
        ]
        current_rows = {
            "campaign": [
                {
                    "dimensions": {"campaign_id": "cmp-1"},
                    "metrics": {"campaign_name": "Core Campaign", "spend": "200", "conversion": "20", "total_purchase_value": "100"},
                }
            ]
        }

        result = build_activity_factor_report(
            "tiktok",
            {"strategy": "targeted_top_objects_changelog", "rows": rows},
            current_rows,
        )

        self.assertEqual(result["activity_strategy"], "targeted_top_objects_changelog")
        self.assertTrue(result["conclusions"])
        self.assertIn("Top", result["conclusions"][0])
        self.assertLessEqual(len(result["conclusions"]), 4)
        self.assertTrue(result["daily_breakdown_candidates"])

    def test_daily_breakdown_adds_trend_to_activity_factor(self) -> None:
        rows = [
            {
                "Time": "2026-05-10 01:15",
                "log_object_type": "Campaign",
                "Object ID": "cmp-1",
                "Object": "Budget",
                "Operator": "Grace",
            },
            {
                "Time": "2026-05-10 03:15",
                "log_object_type": "Campaign",
                "Object ID": "cmp-1",
                "Object": "Budget",
                "Operator": "Grace",
            },
        ]
        current_rows = {
            "campaign": [
                {
                    "dimensions": {"campaign_id": "cmp-1"},
                    "metrics": {"campaign_name": "Core Campaign", "spend": "260", "conversion": "20", "complete_payment_roas": "1.2"},
                }
            ]
        }
        daily_breakdown = {
            "rows": [
                {
                    "level": "campaign",
                    "object_id": "cmp-1",
                    "daily_rows": [
                        {"dimensions": {"campaign_id": "cmp-1", "stat_time_day": "2026-05-09"}, "metrics": {"spend": "50", "conversion": "2", "complete_payment_roas": "0.5"}},
                        {"dimensions": {"campaign_id": "cmp-1", "stat_time_day": "2026-05-10"}, "metrics": {"spend": "100", "conversion": "8", "complete_payment_roas": "1.1"}},
                        {"dimensions": {"campaign_id": "cmp-1", "stat_time_day": "2026-05-11"}, "metrics": {"spend": "110", "conversion": "10", "complete_payment_roas": "1.2"}},
                    ],
                }
            ]
        }

        result = build_activity_factor_report(
            "tiktok",
            {"strategy": "targeted_top_objects_changelog", "rows": rows},
            current_rows,
            daily_breakdown=daily_breakdown,
        )

        factor = result["factors"][0]
        self.assertIn("daily_trend", factor)
        self.assertIn("操作后", factor["daily_trend"]["interpretation"])
        self.assertIn("分天趋势", result["conclusions"][1])


if __name__ == "__main__":
    unittest.main()
