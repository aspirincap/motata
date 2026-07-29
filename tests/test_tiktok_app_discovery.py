from __future__ import annotations

import unittest

from motata_cli.__main__ import build_parser
from motata_cli.tiktok import commands as tiktok_commands
from motata_cli.tiktok.app_discovery import (
    build_tiktok_app_report,
    discover_operable_advertiser_ids,
    discover_recent_spend_advertisers,
)


class FakeTikTokAppClient:
    def list_business_centers(self):
        return {
            "data": {
                "list": [
                    {"bc_info": {"bc_id": "bc-1", "name": "Main BC"}, "user_role": "ADMIN"},
                ]
            }
        }

    def list_bc_assets(self, bc_id, *, asset_type, page=None, page_size=None):
        return {
            "data": {
                "list": [
                    {
                        "asset_id": "adv-1",
                        "asset_name": "Advertiser One",
                        "asset_type": "ADVERTISER",
                        "advertiser_role": "ADMIN",
                    },
                    {
                        "asset_id": "adv-2",
                        "asset_name": "Advertiser Two",
                        "asset_type": "ADVERTISER",
                        "advertiser_role": "ANALYST",
                    },
                ],
                "page_info": {"page": 1, "total_page": 1},
            }
        }

    def integrated_report(self, *args, **kwargs):
        if args and args[0] == "BC":
            return {
                "data": {
                    "list": [
                        {
                            "dimensions": {"advertiser_id": "adv-1"},
                            "metrics": {"spend": "12", "impressions": "100", "reach": "80"},
                        },
                        {
                            "dimensions": {"advertiser_id": "adv-zero"},
                            "metrics": {"spend": "0", "impressions": "10", "reach": "8"},
                        },
                    ],
                    "page_info": {"total_page": 1},
                }
            }
        return {
            "data": {
                "list": [
                    {
                        "dimensions": {"campaign_id": "cmp-1"},
                        "metrics": {"spend": "12", "impressions": "100", "clicks": "5", "conversion": "1"},
                    }
                ]
            }
        }

    def list_campaigns(self, advertiser_id, *, filtering=None, page=None, page_size=None, fields=None, **kwargs):
        return {
            "data": {
                "list": [
                    {
                        "campaign_id": "cmp-1",
                        "campaign_name": "app campaign",
                        "objective_type": "APP_PROMOTION",
                    }
                ]
            }
        }

    def list_adgroups(self, advertiser_id, *, filtering=None, page=None, page_size=None, fields=None, **kwargs):
        return {
            "data": {
                "list": [
                    {
                        "adgroup_id": "ag-1",
                        "campaign_id": "cmp-1",
                        "promotion_type": "APP_ANDROID",
                        "app_id": "app-1",
                        "app_name": "Demo App",
                        "app_download_url": "https://play.google.com/store/apps/details?id=demo.app",
                    }
                ]
            }
        }

    def list_ads(self, advertiser_id, *, filtering=None, page=None, page_size=None, fields=None, **kwargs):
        return {"data": {"list": [{"ad_id": "ad-1", "adgroup_id": "ag-1", "campaign_id": "cmp-1"}]}}


class ReportAttributeTikTokAppClient(FakeTikTokAppClient):
    def __init__(self) -> None:
        self.list_campaigns_called = False

    def integrated_report(self, *args, **kwargs):
        if args and args[0] == "BC":
            return super().integrated_report(*args, **kwargs)
        return {
            "data": {
                "list": [
                    {
                        "dimensions": {"campaign_id": "cmp-1"},
                        "metrics": {
                            "spend": "12",
                            "impressions": "100",
                            "clicks": "5",
                            "conversion": "1",
                            "campaign_name": "app campaign from report",
                            "objective_type": "APP_PROMOTION",
                            "campaign_automation_type": "MANUAL",
                        },
                    }
                ]
            }
        }

    def list_campaigns(self, *args, **kwargs):
        self.list_campaigns_called = True
        raise AssertionError("campaign/get should be skipped when report attributes include campaign detail")


class TikTokAppDiscoveryTests(unittest.TestCase):
    def test_apps_analyze_command_is_exposed(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "tiktok",
                "apps",
                "analyze",
                "--access-token",
                "demo-token",
                "--advertiser-limit",
                "2",
                "--campaign-limit",
                "20",
            ]
        )

        self.assertIs(args.func, tiktok_commands.command_tiktok_apps_analyze)
        self.assertEqual(args.advertiser_limit, 2)
        self.assertEqual(args.campaign_limit, 20)

    def test_discovers_operable_advertisers_from_bc_assets(self) -> None:
        client = FakeTikTokAppClient()

        rows = discover_operable_advertiser_ids(client)

        self.assertEqual([row["advertiser_id"] for row in rows], ["adv-1", "adv-2"])
        self.assertEqual(rows[0]["bc_id"], "bc-1")

    def test_discovers_recent_spend_advertisers_from_bc_report(self) -> None:
        client = FakeTikTokAppClient()

        rows, errors = discover_recent_spend_advertisers(
            client,
            start_date="2026-04-26",
            end_date="2026-05-09",
            advertiser_limit=10,
        )

        self.assertEqual(errors, [])
        self.assertEqual([row["advertiser_id"] for row in rows], ["adv-1"])
        self.assertEqual(rows[0]["spend"], 12.0)

    def test_report_groups_spend_by_app(self) -> None:
        client = FakeTikTokAppClient()

        report = build_tiktok_app_report(
            client,
            start_date="2026-04-26",
            end_date="2026-05-09",
            advertiser_limit=1,
            campaign_limit=20,
        )

        self.assertEqual(report["app_count"], 1)
        self.assertEqual(report["rows"][0]["app_ids"], ["app-1"])
        self.assertEqual(report["rows"][0]["app_names"], ["Demo App"])
        self.assertEqual(report["rows"][0]["spend"], 12.0)

    def test_report_uses_campaign_attributes_without_campaign_get(self) -> None:
        client = ReportAttributeTikTokAppClient()

        report = build_tiktok_app_report(
            client,
            start_date="2026-04-26",
            end_date="2026-05-09",
            advertiser_limit=1,
            campaign_limit=20,
            include_campaigns=True,
        )

        self.assertFalse(client.list_campaigns_called)
        self.assertEqual(report["app_count"], 1)
        campaign = report["rows"][0]["campaigns"][0]
        self.assertEqual(campaign["campaign_name"], "app campaign from report")
        self.assertEqual(campaign["objective_type"], "APP_PROMOTION")


if __name__ == "__main__":
    unittest.main()
