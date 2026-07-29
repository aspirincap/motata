from __future__ import annotations

import unittest

from motata_cli.__main__ import build_parser
from motata_cli.tiktok import commands as tiktok_commands
from motata_cli.tiktok.user_type import build_tiktok_user_type_report


class FakeTikTokUserTypeClient:
    def integrated_report(self, report_type, **params):
        level = params.get("data_level")
        if level == "AUCTION_CAMPAIGN":
            return {
                "data": {
                    "list": [
                        {
                            "dimensions": {"campaign_id": "cmp-drama"},
                            "metrics": {"spend": "120", "impressions": "1000", "clicks": "40", "conversion": "5"},
                        },
                        {
                            "dimensions": {"campaign_id": "cmp-shop"},
                            "metrics": {"spend": "80", "impressions": "800", "clicks": "32", "conversion": "3"},
                        },
                    ]
                }
            }
        if level == "AUCTION_AD":
            return {
                "data": {
                    "list": [
                        {
                            "dimensions": {"ad_id": "ad-drama"},
                            "metrics": {
                                "spend": "120",
                                "impressions": "1000",
                                "clicks": "40",
                                "conversion": "5",
                                "complete_payment": "2",
                                "complete_payment_roas": "1.1",
                                "value_per_complete_payment": "0",
                            },
                        },
                        {
                            "dimensions": {"ad_id": "ad-shop"},
                            "metrics": {
                                "spend": "80",
                                "impressions": "800",
                                "clicks": "32",
                                "conversion": "3",
                                "complete_payment": "1",
                                "complete_payment_roas": "1.0",
                                "value_per_complete_payment": "0",
                            },
                        },
                    ]
                }
            }
        raise AssertionError(f"unexpected report level: {level}")

    def list_campaigns(self, advertiser_id, *, filtering=None, page=None, page_size=None, fields=None, exclude_field_types=None, smart_plus=False):
        ids = set((filtering or {}).get("campaign_ids") or [])
        rows = []
        if "cmp-drama" in ids:
            rows.append(
                {
                    "campaign_id": "cmp-drama",
                    "campaign_name": "Short Drama Episode Romance",
                    "objective_type": "APP_PROMOTION",
                    "campaign_automation_type": "UPGRADED_SMART_PLUS",
                }
            )
        if "cmp-shop" in ids:
            rows.append(
                {
                    "campaign_id": "cmp-shop",
                    "campaign_name": "Shopify checkout sale",
                    "objective_type": "WEB_CONVERSIONS",
                    "campaign_automation_type": "REGULAR",
                }
            )
        return {"data": {"list": rows}}

    def list_adgroups(self, advertiser_id, *, filtering=None, page=None, page_size=None, fields=None, exclude_field_types=None, smart_plus=False):
        ids = set((filtering or {}).get("campaign_ids") or [])
        rows = []
        if "cmp-drama" in ids:
            rows.append(
                {
                    "adgroup_id": "ag-drama",
                    "campaign_id": "cmp-drama",
                    "campaign_name": "Short Drama Episode Romance",
                    "promotion_type": "APP_PROMOTION",
                    "app_id": "app-drama",
                    "app_name": "DramaBox Episodes",
                    "app_download_url": "https://apps.apple.com/us/app/dramabox/id1234567890",
                }
            )
        if "cmp-shop" in ids:
            rows.append(
                {
                    "adgroup_id": "ag-shop",
                    "campaign_id": "cmp-shop",
                    "campaign_name": "Shopify checkout sale",
                    "promotion_type": "WEBSITE",
                }
            )
        return {"data": {"list": rows}}

    def list_ads(self, advertiser_id, *, filtering=None, page=None, page_size=None, fields=None, exclude_field_types=None, smart_plus=False):
        ad_ids = set((filtering or {}).get("ad_ids") or [])
        rows = []
        if "ad-drama" in ad_ids:
            rows.append(
                {
                    "ad_id": "ad-drama",
                    "ad_name": "watch short drama episodes",
                    "campaign_id": "cmp-drama",
                    "campaign_name": "Short Drama Episode Romance",
                    "adgroup_id": "ag-drama",
                    "landing_page_url": "https://apps.apple.com/us/app/dramabox/id1234567890",
                    "ad_text": "Watch dramatic romance episodes now",
                }
            )
        if "ad-shop" in ad_ids:
            rows.append(
                {
                    "ad_id": "ad-shop",
                    "ad_name": "discount product ad",
                    "campaign_id": "cmp-shop",
                    "campaign_name": "Shopify checkout sale",
                    "adgroup_id": "ag-shop",
                    "landing_page_url": "https://shop.example/products/demo?utm_source=tiktok",
                    "ad_text": "Buy product with sale discount",
                }
            )
        return {"data": {"list": rows}}


class TikTokUserTypeTests(unittest.TestCase):
    def test_tiktok_user_type_command_is_exposed(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "tiktok",
                "user-type",
                "analyze",
                "--access-token",
                "demo-token",
                "--advertiser-id",
                "adv-1",
                "--campaign-limit",
                "10",
                "--ad-limit",
                "5",
            ]
        )

        self.assertIs(args.func, tiktok_commands.command_tiktok_user_type_analyze)
        self.assertEqual(args.advertiser_ids, ["adv-1"])
        self.assertEqual(args.campaign_limit, 10)
        self.assertEqual(args.ad_limit, 5)

    def test_report_combines_tiktok_app_and_landing_evidence(self) -> None:
        def fake_scraper(url: str) -> dict:
            if "apps.apple.com" in url:
                return {
                    "name": "DramaBox Episodes",
                    "description": "Watch short drama episodes, romance series, and mini drama stories.",
                    "url_type": "appstore",
                }
            return {
                "name": "Demo Product",
                "description": "Shop product sale discount checkout.",
                "url_type": "ecommerce",
            }

        report = build_tiktok_user_type_report(
            FakeTikTokUserTypeClient(),
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
            campaign_limit=10,
            ad_limit=5,
            content_limit=10,
            product_scraper=fake_scraper,
        )

        self.assertEqual(report["campaign_count"], 2)
        self.assertEqual(report["scraped_content_count"], 2)
        top_types = [item["type"] for item in report["top_types"]]
        self.assertIn("短剧", top_types)
        self.assertIn("电商", top_types)
        self.assertEqual(report["landing_pages"][0]["url"], "https://shop.example/products/demo")
        self.assertEqual(report["app_rows"][0]["app_urls"], ["https://apps.apple.com/us/app/dramabox/id1234567890"])
        self.assertEqual(report["landing_skipped_url_probe_count"], 1)


if __name__ == "__main__":
    unittest.main()
