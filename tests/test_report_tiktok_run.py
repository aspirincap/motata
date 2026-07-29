from __future__ import annotations

import argparse
import unittest
from datetime import date
from unittest.mock import patch

from motata_cli.report.meta import resolve_period
from motata_cli.report.tiktok import TikTokReportRunner, _metric_sets, tiktok_depth_plan


class ReportTikTokRunTests(unittest.TestCase):
    def _args(self, *, depth: str = "standard", dry_run: bool = True) -> argparse.Namespace:
        return argparse.Namespace(
            advertiser_id="7397339180147212304",
            access_token="demo-token",
            period="weekly",
            depth=depth,
            since=None,
            until=None,
            previous_since=None,
            previous_until=None,
            compare=True,
            run_dir=None,
            dry_run=dry_run,
            retry=1,
            retry_wait=0,
            top_objects=None,
            page_size=None,
            smart_plus=False,
            include_previews=None,
            include_product=False,
        )

    def test_weekly_period_matches_meta_contract(self) -> None:
        args = argparse.Namespace(
            period="weekly",
            since=None,
            until=None,
            previous_since=None,
            previous_until=None,
            compare=True,
        )

        window = resolve_period(args, today=date(2026, 5, 13))

        self.assertEqual(window.since, "2026-05-06")
        self.assertEqual(window.until, "2026-05-12")
        self.assertEqual(window.previous_since, "2026-04-29")
        self.assertEqual(window.previous_until, "2026-05-05")

    def test_standard_depth_targets_top_creative_retention_only(self) -> None:
        plan = tiktok_depth_plan("standard")

        self.assertEqual(plan.insight_levels, ("advertiser", "campaign", "adgroup", "ad"))
        self.assertEqual(plan.audience_breakdowns, ("country", "age_gender", "placement"))
        self.assertTrue(plan.include_creative_retention)
        self.assertEqual(plan.structure_mode, "top")

    def test_ad_level_report_metrics_do_not_mix_ad_id_v2_with_ad_id_dimension(self) -> None:
        metric_sets = _metric_sets("ad")

        self.assertTrue(metric_sets)
        self.assertTrue(all("ad_id_v2" not in metrics for metrics in metric_sets))
        self.assertTrue(all("ad_url_list" not in metrics for metrics in metric_sets))

    def test_ad_v2_report_uses_asset_dimension_without_ad_id_metric(self) -> None:
        metric_sets = _metric_sets("ad_v2")

        self.assertTrue(metric_sets)
        self.assertTrue(all("ad_id" not in metrics for metrics in metric_sets))
        self.assertTrue(all("ad_id_v2" not in metrics for metrics in metric_sets))

    def test_dry_run_lists_expected_weekly_sources(self) -> None:
        args = self._args()
        args.tiktok_report_mode = "auction"

        payload = TikTokReportRunner(args).dry_run_payload()
        source_names = [item["name"] for item in payload["sources"]]

        self.assertTrue(payload["options"]["include_previews"])
        self.assertEqual(payload["options"]["include_previews_source"], "depth_default")
        self.assertIn("activities", source_names)
        self.assertIn("activity_targeted_insights", source_names)
        self.assertIn("activity_daily_breakdown", source_names)
        self.assertIn("activity_factors", source_names)
        self.assertIn("current_ad_insights", source_names)
        self.assertIn("current_ad_v2_insights", source_names)
        self.assertIn("previous_ad_insights", source_names)
        self.assertIn("audience_breakdown", source_names)
        self.assertIn("landing_pages", source_names)
        self.assertIn("targeted_creative_retention", source_names)

    def test_standard_activity_pull_targets_top_objects_only(self) -> None:
        args = self._args(dry_run=False)
        runner = TikTokReportRunner(args)
        current_rows = {
            "campaign": [
                {"dimensions": {"campaign_id": "cmp-1"}, "metrics": {"spend": "100", "conversion": "9"}},
            ],
            "adgroup": [
                {"dimensions": {"adgroup_id": "ag-1"}, "metrics": {"spend": "90", "conversion": "8"}},
            ],
            "ad": [
                {"dimensions": {"ad_id": "ad-1"}, "metrics": {"ad_id_v2": "adv2-1", "spend": "80", "conversion": "7"}},
            ],
        }
        calls: list[dict] = []

        def fake_build(client, **kwargs):
            calls.append(kwargs)
            object_id = (kwargs.get("object_ids") or [""])[0]
            return {"status": "success", "task_id": f"task-{object_id}", "rows": [{"Object ID": object_id, "Object": "Budget"}]}

        with patch("motata_cli.report.tiktok.build_tiktok_activities_report", side_effect=fake_build):
            payload = runner._pull_activities(object(), "7397339180147212304", current_rows)

        self.assertEqual(payload["strategy"], "targeted_top_objects_changelog")
        self.assertEqual(payload["target_object_count"], 4)
        self.assertEqual({call["object_type"] for call in calls}, {"CAMPAIGN", "ADGROUP", "AD"})
        self.assertTrue(all(call["operation_types"] == ["CREATE", "STATUS", "UPDATE"] for call in calls))
        self.assertTrue(all(call.get("module") is None for call in calls))
        ad_call = next(call for call in calls if call["object_type"] == "AD")
        self.assertEqual(ad_call["object_ids"], ["ad-1", "adv2-1"])

    def test_deep_activity_pull_keeps_broad_changelog(self) -> None:
        args = self._args(depth="deep", dry_run=False)
        runner = TikTokReportRunner(args)
        calls: list[dict] = []

        def fake_build(client, **kwargs):
            calls.append(kwargs)
            return {"status": "success", "task_id": "task-broad", "rows": []}

        with patch("motata_cli.report.tiktok.build_tiktok_activities_report", side_effect=fake_build):
            payload = runner._pull_activities(object(), "7397339180147212304", {"campaign": [], "adgroup": [], "ad": []})

        self.assertEqual(payload["task_id"], "task-broad")
        self.assertNotIn("object_ids", calls[0])
        self.assertNotIn("operation_types", calls[0])

    def test_activity_daily_breakdown_uses_stat_time_day_dimensions(self) -> None:
        args = self._args(dry_run=False)
        runner = TikTokReportRunner(args)

        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def integrated_report(self, report_type: str, **kwargs):
                self.calls.append({"report_type": report_type, **kwargs})
                return {
                    "data": {
                        "list": [
                            {
                                "dimensions": {"campaign_id": "cmp-1", "stat_time_day": "2026-05-10"},
                                "metrics": {"spend": "100", "conversion": "5"},
                            }
                        ]
                    }
                }

        client = FakeClient()
        activities = {
            "rows": [
                {
                    "Time": "2026-05-10 10:00:00",
                    "log_object_type": "Campaign",
                    "Object ID": "cmp-1",
                    "Object": "Budget",
                }
            ]
        }

        payload = runner._pull_activity_daily_breakdown(client, "7397339180147212304", activities)

        self.assertEqual(payload["strategy"], "activity_target_daily_breakdown")
        self.assertEqual(payload["pulled_count"], 1)
        self.assertEqual(client.calls[0]["dimensions"], ["campaign_id", "stat_time_day"])
        self.assertEqual(payload["rows"][0]["daily_rows"][0]["dimensions"]["stat_time_day"], "2026-05-10")

    def test_fast_depth_defaults_previews_off_and_cli_can_disable_standard(self) -> None:
        base = dict(
            advertiser_id="7397339180147212304",
            access_token="demo-token",
            period="weekly",
            since=None,
            until=None,
            previous_since=None,
            previous_until=None,
            compare=True,
            run_dir=None,
            dry_run=True,
            retry=1,
            retry_wait=0,
            top_objects=None,
            page_size=None,
            smart_plus=False,
            include_product=False,
        )

        fast_payload = TikTokReportRunner(argparse.Namespace(**base, depth="fast", include_previews=None)).dry_run_payload()
        standard_no_preview_payload = TikTokReportRunner(
            argparse.Namespace(**base, depth="standard", include_previews=False)
        ).dry_run_payload()

        self.assertFalse(fast_payload["options"]["include_previews"])
        self.assertEqual(fast_payload["options"]["include_previews_source"], "depth_default")
        self.assertFalse(standard_no_preview_payload["options"]["include_previews"])
        self.assertEqual(standard_no_preview_payload["options"]["include_previews_source"], "cli")

    def test_gmv_max_fast_creative_dimensions_use_campaign_and_item_only(self) -> None:
        base = self._args(dry_run=False)
        base.tiktok_report_mode = "gmv_max"
        base.include_gmv_max = "always"
        base.gmv_max_store_ids = []
        base.gmv_max_promotion_types = ["PRODUCT_GMV_MAX"]

        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def gmv_max_report(self, advertiser_id: str, **kwargs):
                self.calls.append(kwargs)
                dimensions = kwargs["dimensions"]
                if dimensions == ["item_group_id"]:
                    return {
                        "data": {
                            "list": [
                                {"dimensions": {"item_group_id": "spu-1"}, "metrics": {"orders": "1", "gross_revenue": "10"}}
                            ],
                            "page_info": {"total_page": 1},
                        }
                    }
                return {
                    "data": {
                        "list": [{"dimensions": {"campaign_id": "cmp-1", "item_id": "item-1"}, "metrics": {"cost": "2"}}],
                        "page_info": {"total_page": 1},
                    }
                }

        fast_args = argparse.Namespace(**vars(base), gmv_max_creative_dimensions="fast")
        official_args = argparse.Namespace(**vars(base), gmv_max_creative_dimensions="official")

        fast_client = FakeClient()
        fast_payload = TikTokReportRunner(fast_args)._pull_gmv_max_targeted_creative_report(
            fast_client,
            "7397339180147212304",
            "creative",
            ("2026-05-06", "2026-05-12"),
            store_ids=["store-1"],
            campaign_ids=["cmp-1"],
            base_filtering={},
        )

        official_client = FakeClient()
        official_payload = TikTokReportRunner(official_args)._pull_gmv_max_targeted_creative_report(
            official_client,
            "7397339180147212304",
            "creative",
            ("2026-05-06", "2026-05-12"),
            store_ids=["store-1"],
            campaign_ids=["cmp-1"],
            base_filtering={},
        )

        self.assertEqual(fast_payload["dimensions"], ["campaign_id", "item_id"])
        self.assertIn({"campaign_ids": ["cmp-1"], "item_group_ids": ["spu-1"]}, [call.get("filtering") for call in fast_client.calls])
        self.assertEqual(official_payload["dimensions"], ["campaign_id", "item_group_id", "item_id"])


if __name__ == "__main__":
    unittest.main()
