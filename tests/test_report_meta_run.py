from __future__ import annotations

import argparse
import unittest
from datetime import date
from unittest.mock import patch

from motata_cli.report.meta import MetaReportRunner, depth_plan, resolve_period


class ReportMetaRunTests(unittest.TestCase):
    def _args(self, *, depth: str = "standard", dry_run: bool = True) -> argparse.Namespace:
        return argparse.Namespace(
            account_id="123",
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
            top_objects=30,
            limit=None,
            include_previews=None,
            include_product=False,
        )

    def test_weekly_period_uses_last_complete_seven_days_and_previous_window(self) -> None:
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

    def test_custom_period_derives_equal_previous_window(self) -> None:
        args = argparse.Namespace(
            period="custom",
            since="2026-05-04",
            until="2026-05-10",
            previous_since=None,
            previous_until=None,
            compare=True,
        )

        window = resolve_period(args)

        self.assertEqual(window.previous_since, "2026-04-27")
        self.assertEqual(window.previous_until, "2026-05-03")

    def test_depth_standard_includes_four_insight_levels_and_core_audience(self) -> None:
        plan = depth_plan("standard")

        self.assertEqual(plan.insight_levels, ("account", "campaign", "adset", "ad"))
        self.assertEqual(plan.audience_breakdowns, ("country", "age_gender", "placement"))
        self.assertEqual(plan.structure_mode, "top")

    def test_dry_run_lists_expected_weekly_sources(self) -> None:
        args = self._args()

        payload = MetaReportRunner(args).dry_run_payload()
        source_names = [item["name"] for item in payload["sources"]]

        self.assertTrue(payload["options"]["include_previews"])
        self.assertEqual(payload["options"]["include_previews_source"], "depth_default")
        self.assertEqual(payload["options"]["preview_fetch_limit"], 30)
        self.assertIn("activities", source_names)
        self.assertIn("activity_targeted_insights", source_names)
        self.assertIn("activity_daily_breakdown", source_names)
        self.assertIn("activity_factors", source_names)
        self.assertIn("current_ad_insights", source_names)
        self.assertIn("previous_ad_insights", source_names)
        self.assertIn("audience_breakdown", source_names)
        self.assertIn("creative_structure", source_names)

    def test_fast_depth_defaults_previews_off_and_cli_can_disable_standard(self) -> None:
        base = dict(
            account_id="123",
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
            top_objects=30,
            limit=None,
            include_product=False,
        )

        fast_payload = MetaReportRunner(argparse.Namespace(**base, depth="fast", include_previews=None)).dry_run_payload()
        standard_no_preview_payload = MetaReportRunner(
            argparse.Namespace(**base, depth="standard", include_previews=False)
        ).dry_run_payload()

        self.assertFalse(fast_payload["options"]["include_previews"])
        self.assertEqual(fast_payload["options"]["include_previews_source"], "depth_default")
        self.assertEqual(fast_payload["options"]["preview_fetch_limit"], 0)
        self.assertFalse(standard_no_preview_payload["options"]["include_previews"])
        self.assertEqual(standard_no_preview_payload["options"]["include_previews_source"], "cli")
        self.assertEqual(standard_no_preview_payload["options"]["preview_fetch_limit"], 0)

    def test_activity_daily_breakdown_uses_meta_time_increment(self) -> None:
        args = self._args(dry_run=False)
        args.period = "custom"
        args.since = "2026-05-07"
        args.until = "2026-05-13"
        args.previous_since = "2026-04-30"
        args.previous_until = "2026-05-06"
        runner = MetaReportRunner(args)
        activities = {
            "rows": [
                {
                    "object_id": "cmp-1",
                    "object_type": "campaign",
                    "translated_event_type": "Campaign budget update",
                    "date_time_in_timezone": "2026-05-10T10:00:00+0800",
                }
            ]
        }
        calls: list[dict] = []

        def fake_daily(meta, object_id, window, *, limit, async_report, metric_fields):
            calls.append(
                {
                    "object_id": object_id,
                    "window": window,
                    "limit": limit,
                    "async_report": async_report,
                    "metric_fields": metric_fields,
                }
            )
            return [{"date_start": "2026-05-10", "spend": "100"}]

        with patch("motata_cli.report.meta._pull_object_daily_insights", side_effect=fake_daily):
            payload = runner._pull_activity_daily_breakdown(object(), activities)

        self.assertEqual(payload["strategy"], "activity_target_daily_breakdown")
        self.assertEqual(payload["pulled_count"], 1)
        self.assertEqual(calls[0]["object_id"], "cmp-1")
        self.assertEqual(calls[0]["window"], ("2026-04-30", "2026-05-13"))
        self.assertEqual(payload["rows"][0]["daily_rows"][0]["date_start"], "2026-05-10")

    def test_activity_daily_breakdown_pulls_meta_adgroup_as_adset(self) -> None:
        runner = MetaReportRunner(self._args(dry_run=False))
        activities = {
            "rows": [
                {
                    "object_id": "adset-1",
                    "object_type": "ADGROUP",
                    "translated_event_type": "广告状态更新",
                    "date_time_in_timezone": "2026/5/12 18:16",
                }
            ]
        }
        calls: list[dict] = []

        def fake_daily(meta, object_id, window, *, limit, async_report, metric_fields):
            calls.append(
                {
                    "object_id": object_id,
                    "window": window,
                    "limit": limit,
                    "async_report": async_report,
                    "metric_fields": metric_fields,
                }
            )
            return [{"date_start": "2026-05-12", "spend": "120"}]

        with patch("motata_cli.report.meta._pull_object_daily_insights", side_effect=fake_daily):
            payload = runner._pull_activity_daily_breakdown(object(), activities)

        self.assertEqual(payload["pulled_count"], 1)
        self.assertEqual(payload["rows"][0]["level"], "adset")
        self.assertEqual(calls[0]["object_id"], "adset-1")


if __name__ == "__main__":
    unittest.main()
