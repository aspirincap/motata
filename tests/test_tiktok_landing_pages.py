from __future__ import annotations

import unittest

from motata_cli.__main__ import build_parser
from motata_cli.meta.commands import CliError
from motata_cli.tiktok import commands as tiktok_commands
from motata_cli.tiktok.landing_pages import (
    _list_ads_by_ids,
    _list_upgraded_smart_plus_ads,
    build_tiktok_landing_page_report,
    canonicalize_landing_url,
    extract_landing_url,
    extract_url_evidence,
)


class FakeTikTokClient:
    def __init__(self) -> None:
        self.adgroup_calls: list[str] = []
        self.ads = {
            "ad-1": {
                "ad_id": "ad-1",
                "ad_name": "ad one",
                "campaign_id": "cmp-1",
                "campaign_name": "campaign",
                "adgroup_id": "ag-1",
                "adgroup_name": "adgroup",
                "landing_page_url": "https://shop.example/products/demo?utm_source=tiktok&variant=1",
            },
            "ad-2": {
                "ad_id": "ad-2",
                "ad_name": "ad two",
                "campaign_id": "cmp-1",
                "campaign_name": "campaign",
                "adgroup_id": "ag-1",
                "adgroup_name": "adgroup",
                "landing_page_urls": ["https://shop.example/products/demo?ttclid=ignored"],
            },
            "ad-3": {
                "ad_id": "ad-3",
                "ad_name": "missing one",
                "campaign_id": "cmp-2",
                "adgroup_id": "ag-missing",
                "video_id": "video-1",
            },
            "ad-4": {
                "ad_id": "ad-4",
                "ad_name": "missing two",
                "campaign_id": "cmp-2",
                "adgroup_id": "ag-missing",
                "image_ids": ["image-1"],
            },
            "ad-5": {
                "ad_id": "ad-5",
                "ad_name": "upgraded smart ad",
                "campaign_id": "cmp-3",
                "adgroup_id": "ag-3",
                "campaign_automation_type": "UPGRADED_SMART_PLUS",
                "smart_plus_ad_id": "sp-ad-5",
            },
        }
        self.smart_plus_calls: list[str] = []

    def integrated_report(self, *args, **kwargs):
        return {
            "data": {
                "list": [
                    {"dimensions": {"ad_id": "ad-1"}, "metrics": {"spend": "10", "impressions": "100", "clicks": "5", "conversion": "1", "complete_payment": "1", "complete_payment_roas": "2.0"}},
                    {"dimensions": {"ad_id": "ad-2"}, "metrics": {"spend": "5", "impressions": "50", "clicks": "1", "conversion": "0"}},
                    {"dimensions": {"ad_id": "ad-3"}, "metrics": {"spend": "7", "impressions": "70", "clicks": "2", "conversion": "0"}},
                    {"dimensions": {"ad_id": "ad-4"}, "metrics": {"spend": "3", "impressions": "30", "clicks": "1", "conversion": "0"}},
                    {"dimensions": {"ad_id": "ad-5"}, "metrics": {"spend": "6", "impressions": "60", "clicks": "3", "conversion": "0"}},
                    {"dimensions": {"ad_id": "ad-zero"}, "metrics": {"spend": "0", "impressions": "10", "clicks": "1", "conversion": "0"}},
                ],
                "page_info": {"total_page": 1},
            }
        }

    def list_ads(self, advertiser_id, *, filtering=None, page=None, page_size=None, smart_plus=False, **kwargs):
        if smart_plus:
            ad_ids = (filtering or {}).get("smart_plus_ad_ids") or []
            self.smart_plus_calls.extend(ad_ids)
            if "sp-ad-5" in ad_ids:
                return {
                    "data": {
                        "list": [
                            {
                                "smart_plus_ad_id": "sp-ad-5",
                                "ad_name": "upgraded smart ad",
                                "campaign_id": "cmp-3",
                                "landing_page_url_list": [
                                    {"landing_page_url": "https://smart.example/products/upgraded?utm_source=tiktok"}
                                ],
                                "creative_list": [
                                    {"video_url": "https://p16-sign-va.tiktokcdn.com/video.mp4"}
                                ],
                            }
                        ]
                    }
                }
            return {"data": {"list": []}}
        ad_ids = (filtering or {}).get("ad_ids") or []
        return {"data": {"list": [self.ads[ad_id] for ad_id in ad_ids if ad_id in self.ads]}}

    def get_ad(self, advertiser_id, ad_id, *, smart_plus=False, **kwargs):
        return self.ads[ad_id]

    def get_adgroup(self, advertiser_id, adgroup_id, *, smart_plus=False, **kwargs):
        self.adgroup_calls.append(adgroup_id)
        return {
            "adgroup_id": adgroup_id,
            "adgroup_name": "fallback adgroup",
            "promotion_type": "WEBSITE",
            "optimization_goal": "CLICK",
        }


class LegacySmartPlusTikTokClient(FakeTikTokClient):
    def __init__(self) -> None:
        super().__init__()
        self.ads = {
            "legacy-1": {
                "ad_id": "legacy-1",
                "ad_name": "legacy one",
                "campaign_id": "cmp-legacy",
                "campaign_name": "legacy campaign",
                "adgroup_id": "ag-legacy",
                "campaign_automation_type": "SMART_PLUS",
            },
            "legacy-2": {
                "ad_id": "legacy-2",
                "ad_name": "legacy two",
                "campaign_id": "cmp-legacy",
                "campaign_name": "legacy campaign",
                "adgroup_id": "ag-legacy",
                "campaign_automation_type": "SMART_PLUS",
            },
        }
        self.spc_calls: list[dict] = []

    def integrated_report(self, *args, **kwargs):
        return {
            "data": {
                "list": [
                    {"dimensions": {"ad_id": "legacy-1"}, "metrics": {"spend": "9", "impressions": "90", "clicks": "6", "conversion": "1"}},
                    {"dimensions": {"ad_id": "legacy-2"}, "metrics": {"spend": "4", "impressions": "40", "clicks": "2", "conversion": "0"}},
                ],
                "page_info": {"total_page": 1},
            }
        }

    def _raw_request(self, method, path, *, params=None, json_body=None, timeout=60):
        if path == "ad/get/":
            return {"data": {"list": []}}
        self.spc_calls.append({"method": method, "path": path, "params": params})
        return {
            "data": {
                "list": [
                    {
                        "campaign_id": "cmp-legacy",
                        "campaign_name": "legacy campaign",
                        "landing_page_url": "https://legacy.example/products/demo?utm_source=tiktok",
                    }
                ]
            }
        }


class UpgradedSmartPlusCreativeTikTokClient(FakeTikTokClient):
    def __init__(self) -> None:
        super().__init__()
        self.ads["creative-6"] = {
            "ad_id": "creative-6",
            "ad_name": "upgraded smart creative",
            "campaign_id": "cmp-6",
            "adgroup_id": "ag-6",
            "campaign_automation_type": "UPGRADED_SMART_PLUS_CREATIVE",
            "ad_id_v2": "sp-ad-6",
        }

    def integrated_report(self, *args, **kwargs):
        payload = super().integrated_report(*args, **kwargs)
        payload["data"]["list"].append(
            {
                "dimensions": {"ad_id": "creative-6"},
                "metrics": {"spend": "8", "impressions": "80", "clicks": "4", "conversion": "1"},
            }
        )
        return payload

    def list_ads(self, advertiser_id, *, filtering=None, page=None, page_size=None, smart_plus=False, **kwargs):
        if smart_plus:
            ad_ids = (filtering or {}).get("smart_plus_ad_ids") or []
            self.smart_plus_calls.extend(ad_ids)
            if "sp-ad-6" in ad_ids:
                return {
                    "data": {
                        "list": [
                            {
                                "smart_plus_ad_id": "sp-ad-6",
                                "ad_id_v2": "sp-ad-6",
                                "ad_name": "upgraded smart creative",
                                "campaign_id": "cmp-6",
                                "landing_page_url_list": [
                                    {"landing_page_url": "https://apps.apple.com/us/app/demo/id123456789?pt=tracking"}
                                ],
                                "creative_list": [
                                    {"video_url": "https://p16-sign-va.tiktokcdn.com/upgraded-video.mp4"}
                                ],
                            }
                        ]
                    }
                }
        return super().list_ads(
            advertiser_id,
            filtering=filtering,
            page=page,
            page_size=page_size,
            smart_plus=smart_plus,
            **kwargs,
        )


class ReportAttributeTikTokClient:
    def __init__(self) -> None:
        self.list_ads_called = False

    def integrated_report(self, *args, **kwargs):
        return {
            "data": {
                "list": [
                    {
                        "dimensions": {"ad_id": "report-ad-1"},
                        "metrics": {
                            "spend": "11",
                            "impressions": "110",
                            "clicks": "7",
                            "conversion": "2",
                            "complete_payment": "1",
                            "complete_payment_roas": "2.5",
                            "ad_name": "report ad one",
                            "campaign_id": "report-cmp-1",
                            "campaign_name": "report campaign",
                            "adgroup_id": "report-ag-1",
                            "adgroup_name": "report adgroup",
                            "promotion_type": "WEBSITE",
                            "ad_url": "https://report.example/products/direct?utm_source=tiktok",
                        },
                    }
                ],
                "page_info": {"total_page": 1},
            }
        }

    def list_ads(self, *args, **kwargs):
        self.list_ads_called = True
        raise AssertionError("ad/get should be skipped when report attributes include a landing URL")


class ReportAttributeFallbackTikTokClient(ReportAttributeTikTokClient):
    def __init__(self) -> None:
        super().__init__()
        self.report_modes: list[list[str]] = []

    def integrated_report(self, *args, **kwargs):
        metrics = list(kwargs.get("metrics") or [])
        self.report_modes.append(metrics)
        if "advertiser_name" in metrics or "billing_event" in metrics:
            raise CliError("metric advertiser_name is not supported for this report")
        self.assert_essential_metrics(metrics)
        return super().integrated_report(*args, **kwargs)

    def assert_essential_metrics(self, metrics: list[str]) -> None:
        required = {"ad_url", "campaign_id", "campaign_name", "adgroup_id", "adgroup_name"}
        missing = required.difference(metrics)
        if missing:
            raise AssertionError(f"missing essential report attributes: {sorted(missing)}")


class PreloadedReportRowsClient(ReportAttributeTikTokClient):
    def integrated_report(self, *args, **kwargs):
        raise AssertionError("landing report should reuse preloaded ad insights")


class AdGetProbeClient:
    def __init__(self) -> None:
        self.list_ads_calls: list[dict] = []
        self.raw_calls: list[dict] = []

    def list_ads(self, advertiser_id, *, filtering=None, page=None, page_size=None, fields=None, smart_plus=False, **kwargs):
        self.list_ads_calls.append(
            {
                "filtering": filtering,
                "page": page,
                "page_size": page_size,
                "fields": fields,
                "smart_plus": smart_plus,
            }
        )
        requested = set((filtering or {}).get("ad_ids") or [])
        rows = []
        if "ad-normal" in requested:
            rows.append({"ad_id": "ad-normal", "ad_name": "normal", "landing_page_url": "https://normal.example"})
        return {"data": {"list": rows}}

    def _raw_request(self, method, path, *, params=None, json_body=None, timeout=60):
        self.raw_calls.append({"method": method, "path": path, "params": params, "timeout": timeout})
        requested = set(((params or {}).get("filtering") or {}).get("ad_ids_v2") or [])
        rows = []
        if "ad-v2" in requested:
            rows.append({"ad_id": "ad-real", "ad_id_v2": "ad-v2", "ad_name": "v2", "landing_page_url": "https://v2.example"})
        return {"data": {"list": rows}}


class SmartPlusRawProbeClient:
    def __init__(self) -> None:
        self.raw_calls: list[dict] = []

    def _raw_request(self, method, path, *, params=None, json_body=None, timeout=60):
        self.raw_calls.append({"method": method, "path": path, "params": params, "timeout": timeout})
        filtering = (params or {}).get("filtering") or {}
        if filtering == {"smart_plus_ad_ids": ["sp-ad-1"]}:
            return {
                "data": {
                    "list": [
                        {
                            "smart_plus_ad_id": "sp-ad-1",
                            "campaign_id": "cmp-sp",
                            "landing_page_url_list": [{"landing_page_url": "https://smart.example/direct"}],
                        }
                    ]
                }
            }
        return {"data": {"list": []}}


class TikTokLandingPageParserTests(unittest.TestCase):
    def test_landing_pages_analyze_command_is_exposed(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "tiktok",
                "landing-pages",
                "analyze",
                "--access-token",
                "demo-token",
                "--advertiser-id",
                "123",
                "--advertiser-id",
                "456",
                "--since",
                "2026-04-26",
                "--until",
                "2026-05-09",
            ]
        )

        self.assertIs(args.func, tiktok_commands.command_tiktok_landing_pages_analyze)
        self.assertEqual(args.advertiser_ids, ["123", "456"])
        self.assertEqual(args.start_date, "2026-04-26")
        self.assertEqual(args.end_date, "2026-05-09")
        self.assertEqual(args.advertiser_limit, 10)
        self.assertTrue(args.no_product)

    def test_landing_pages_product_enrichment_is_explicit(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "tiktok",
                "landing-pages",
                "analyze",
                "--access-token",
                "demo-token",
                "--advertiser-id",
                "123",
                "--product",
            ]
        )

        self.assertFalse(args.no_product)


class TikTokLandingPageExtractionTests(unittest.TestCase):
    def test_canonicalize_removes_tracking_query(self) -> None:
        self.assertEqual(
            canonicalize_landing_url("http://Shop.Example/products/demo?utm_source=tiktok&variant=1&ttclid=abc"),
            "https://shop.example/products/demo",
        )

    def test_extract_landing_url_prefers_landing_fields_and_filters_assets(self) -> None:
        url, source = extract_landing_url(
            {
                "video_url": "https://p16-sign-va.tiktokcdn.com/video.mp4",
                "landing_page_url": "https://shop.example/products/demo?utm_campaign=ignored",
            }
        )

        self.assertEqual(url, "https://shop.example/products/demo")
        self.assertEqual(source, "landing_page_url")

    def test_extract_url_evidence_classifies_creative_and_ad_urls(self) -> None:
        evidence = extract_url_evidence(
            {
                "ad_configuration": {"ad_url": "https://landing.example/path?utm_source=tiktok"},
                "creative_list": [{"video_url": "https://p16-sign-va.tiktokcdn.com/video.mp4"}],
            }
        )

        kinds = {item["source"]: item["kind"] for item in evidence}
        self.assertEqual(kinds["ad_configuration.ad_url"], "landing")
        self.assertEqual(kinds["creative_list[0].video_url"], "creative_asset")


class TikTokLandingPageReportTests(unittest.TestCase):
    def test_ad_get_tries_ad_ids_before_ad_ids_v2_for_unresolved_only(self) -> None:
        client = AdGetProbeClient()

        details = _list_ads_by_ids(client, "adv-1", ["ad-normal", "ad-v2"], smart_plus=False)

        self.assertEqual(set(details), {"ad-normal", "ad-v2"})
        self.assertEqual(client.list_ads_calls[0]["filtering"], {"ad_ids": ["ad-normal", "ad-v2"]})
        self.assertNotIn("ad_id_v2", client.list_ads_calls[0]["fields"])
        self.assertEqual(len(client.raw_calls), 1)
        self.assertEqual(client.raw_calls[0]["path"], "ad/get/")
        self.assertEqual(client.raw_calls[0]["params"]["filtering"], {"ad_ids_v2": ["ad-v2"]})
        self.assertNotIn("ad_id_v2", client.raw_calls[0]["params"]["fields"])

    def test_ad_get_cache_skips_duplicate_fetches(self) -> None:
        client = AdGetProbeClient()
        cache: dict[str, dict] = {}

        _list_ads_by_ids(client, "adv-1", ["ad-normal"], smart_plus=False, cache=cache)
        _list_ads_by_ids(client, "adv-1", ["ad-normal"], smart_plus=False, cache=cache)

        self.assertEqual(len(client.list_ads_calls), 1)
        self.assertEqual(client.raw_calls, [])

    def test_smart_plus_ad_get_prefers_smart_plus_ad_ids_filter(self) -> None:
        client = SmartPlusRawProbeClient()

        details = _list_upgraded_smart_plus_ads(client, "adv-1", ["sp-ad-1"])

        self.assertIn("sp-ad-1", details)
        self.assertEqual(client.raw_calls[0]["path"], "smart_plus/ad/get/")
        self.assertEqual(client.raw_calls[0]["params"]["filtering"], {"smart_plus_ad_ids": ["sp-ad-1"]})
        self.assertEqual(extract_landing_url(details["sp-ad-1"])[0], "https://smart.example/direct")

    def test_smart_plus_ad_get_ignores_non_rich_cached_normal_ad_detail(self) -> None:
        client = SmartPlusRawProbeClient()
        cache = {
            "sp-ad-1": {
                "ad_id": "creative-1",
                "smart_plus_ad_id": "sp-ad-1",
                "campaign_automation_type": "UPGRADED_SMART_PLUS_CREATIVE",
            }
        }

        details = _list_upgraded_smart_plus_ads(client, "adv-1", ["sp-ad-1"], cache=cache)

        self.assertEqual(client.raw_calls[0]["path"], "smart_plus/ad/get/")
        self.assertEqual(extract_landing_url(details["sp-ad-1"])[0], "https://smart.example/direct")

    def test_report_groups_ads_by_landing_url_and_lists_unresolved(self) -> None:
        client = FakeTikTokClient()

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
        )

        self.assertEqual(report["ad_count"], 5)
        self.assertEqual(report["group_count"], 2)
        self.assertEqual(report["no_url_count"], 2)
        row = next(row for row in report["rows"] if row["url"] == "https://shop.example/products/demo")
        self.assertEqual(row["url"], "https://shop.example/products/demo")
        self.assertEqual(row["ad_count"], 2)
        self.assertEqual(row["spend"], 15.0)
        self.assertEqual(row["impressions"], 150)
        self.assertEqual(row["clicks"], 6)
        self.assertEqual(row["conversions"], 1.0)
        self.assertEqual(row["complete_payment"], 1.0)
        self.assertEqual(row["revenue"], 20.0)
        self.assertEqual(row["roas"], 1.33)
        self.assertEqual(report["no_url"][0]["ad_id"], "ad-3")

    def test_report_uses_smart_plus_ad_get_for_upgraded_smart_plus_ads(self) -> None:
        client = FakeTikTokClient()

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
        )

        self.assertIn("sp-ad-5", client.smart_plus_calls)
        smart_row = next(row for row in report["rows"] if row["url"] == "https://smart.example/products/upgraded")
        self.assertEqual(smart_row["url_source"], "landing_page_url_list[0].landing_page_url")
        self.assertEqual(smart_row["spend"], 6.0)
        self.assertEqual(report["smart_plus_detail_count"], 1)
        self.assertEqual(report["upgraded_smart_plus_ad_count"], 1)
        self.assertEqual(smart_row["creative_url_ad_count"], 1)
        evidence = smart_row["top_ads"][0]["url_evidence"]
        self.assertTrue(any(item["kind"] == "creative_asset" for item in evidence))

    def test_report_preserves_upgraded_smart_plus_creative_identity_and_app_url(self) -> None:
        client = UpgradedSmartPlusCreativeTikTokClient()

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
        )

        self.assertIn("sp-ad-6", client.smart_plus_calls)
        app_row = next(row for row in report["rows"] if row["url"].startswith("https://apps.apple.com/us/app/demo/id123456789"))
        top_ad = app_row["top_ads"][0]
        identity = top_ad["upgraded_smart_plus_identity"]
        self.assertEqual(identity["campaign_automation_type"], "UPGRADED_SMART_PLUS_CREATIVE")
        self.assertEqual(identity["report_ad_id"], "creative-6")
        self.assertEqual(identity["smart_plus_ad_id"], "sp-ad-6")
        self.assertEqual(identity["creative_id"], "creative-6")
        self.assertTrue(any(item["kind"] == "creative_asset" for item in top_ad["url_evidence"]))
        self.assertGreaterEqual(report["app_store_url_ad_count"], 1)

    def test_report_uses_campaign_spc_get_once_for_legacy_smart_plus_campaigns(self) -> None:
        client = LegacySmartPlusTikTokClient()

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
        )

        self.assertEqual(len(client.spc_calls), 1)
        self.assertEqual(client.spc_calls[0]["path"], "campaign/spc/get/")
        self.assertEqual(client.spc_calls[0]["params"]["campaign_ids"], ["cmp-legacy"])
        self.assertEqual(report["campaign_spc_probe_count"], 1)
        self.assertEqual(report["no_url_count"], 0)
        self.assertEqual(client.adgroup_calls, [])
        legacy_row = next(row for row in report["rows"] if row["url"] == "https://legacy.example/products/demo")
        self.assertEqual(legacy_row["url_source"], "campaign_spc.landing_page_url")
        self.assertEqual(legacy_row["ad_count"], 2)
        self.assertEqual(legacy_row["spend"], 13.0)

    def test_report_caches_repeated_adgroup_probe_for_no_url_ads(self) -> None:
        client = FakeTikTokClient()

        build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
        )

        self.assertEqual(client.adgroup_calls, ["ag-missing"])

    def test_report_skips_known_app_campaigns_when_requested(self) -> None:
        client = FakeTikTokClient()

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
            skip_campaign_ids={"cmp-1", "cmp-3"},
        )

        self.assertEqual(report["skipped_url_probe_count"], 3)
        self.assertEqual(report["group_count"], 0)
        self.assertEqual(report["no_url_count"], 2)
        self.assertEqual(client.smart_plus_calls, [])

    def test_report_uses_report_attribute_url_without_ad_get(self) -> None:
        client = ReportAttributeTikTokClient()

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
        )

        self.assertFalse(client.list_ads_called)
        self.assertEqual(report["report_direct_url_count"], 1)
        self.assertEqual(report["skipped_ad_detail_fetch_count"], 1)
        self.assertEqual(report["ad_count"], 1)
        self.assertEqual(report["group_count"], 1)
        row = report["rows"][0]
        self.assertEqual(row["url"], "https://report.example/products/direct")
        self.assertEqual(row["url_source"], "landing_page_url")
        self.assertEqual(row["revenue"], 27.5)

    def test_report_retries_with_essential_attribute_url_before_ad_get(self) -> None:
        client = ReportAttributeFallbackTikTokClient()

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
        )

        self.assertFalse(client.list_ads_called)
        self.assertEqual(report["report_attribute_modes"]["adv-1"], "essential_attributes")
        self.assertEqual(report["report_direct_url_count"], 1)
        self.assertEqual(report["ad_detail_fetch_count"], 0)
        self.assertEqual(report["rows"][0]["url"], "https://report.example/products/direct")
        self.assertGreaterEqual(len(client.report_modes), 2)

    def test_report_reuses_preloaded_ad_insights_without_report_call(self) -> None:
        client = PreloadedReportRowsClient()
        preloaded_rows = [
            {
                "dimensions": {"ad_id": "report-ad-1"},
                "metrics": {
                    "spend": "11",
                    "impressions": "110",
                    "clicks": "7",
                    "conversion": "2",
                    "complete_payment": "1",
                    "complete_payment_roas": "2.5",
                    "ad_name": "report ad one",
                    "campaign_id": "report-cmp-1",
                    "campaign_name": "report campaign",
                    "adgroup_id": "report-ag-1",
                    "adgroup_name": "report adgroup",
                    "promotion_type": "WEBSITE",
                    "ad_url": "https://report.example/products/direct?utm_source=tiktok",
                },
            }
        ]

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
            preloaded_report_rows=preloaded_rows,
        )

        self.assertEqual(report["report_attribute_modes"]["adv-1"], "preloaded_ad_insights")
        self.assertFalse(client.list_ads_called)
        self.assertEqual(report["rows"][0]["url"], "https://report.example/products/direct")

    def test_report_uses_ad_url_list_and_purchase_value_without_ad_get(self) -> None:
        client = PreloadedReportRowsClient()
        preloaded_rows = [
            {
                "dimensions": {"ad_id": "report-ad-list"},
                "metrics": {
                    "spend": "20",
                    "impressions": "200",
                    "clicks": "10",
                    "conversion": "3",
                    "total_purchase_value": "88",
                    "campaign_id": "report-cmp-2",
                    "campaign_name": "report campaign",
                    "adgroup_id": "report-ag-2",
                    "adgroup_name": "report adgroup",
                    "ad_url_list": [{"url": "https://list.example/products/direct?utm_source=tiktok"}],
                },
            }
        ]

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
            preloaded_report_rows=preloaded_rows,
        )

        self.assertFalse(client.list_ads_called)
        self.assertEqual(report["rows"][0]["url"], "https://list.example/products/direct")
        self.assertEqual(report["rows"][0]["revenue"], 88.0)
        self.assertEqual(report["rows"][0]["roas"], 4.4)

    def test_report_uses_preloaded_ad_id_v2_rows_for_smart_plus_landing(self) -> None:
        client = SmartPlusRawProbeClient()
        asset_rows = [
            {
                "dimensions": {"ad_id_v2": "sp-ad-1"},
                "metrics": {
                    "spend": "20",
                    "impressions": "200",
                    "clicks": "10",
                    "conversion": "3",
                    "total_purchase_value": "88",
                    "campaign_automation_type": "UPGRADED_SMART_PLUS",
                    "campaign_id": "cmp-sp",
                    "campaign_name": "smart campaign",
                    "adgroup_id": "ag-sp",
                    "adgroup_name": "smart adgroup",
                    "ad_name": "smart asset",
                    "ad_url": "-",
                },
            }
        ]

        report = build_tiktok_landing_page_report(
            client,
            advertiser_ids=["adv-1"],
            start_date="2026-04-26",
            end_date="2026-05-09",
            preloaded_asset_report_rows=asset_rows,
        )

        self.assertEqual(report["report_attribute_modes"]["adv-1"], "preloaded_ad_id_v2_insights")
        self.assertEqual(report["ad_detail_fetch_count"], 0)
        self.assertEqual(report["smart_plus_detail_count"], 1)
        self.assertEqual(report["group_count"], 1)
        self.assertEqual(report["no_url_count"], 0)
        self.assertEqual(report["rows"][0]["url"], "https://smart.example/direct")
        self.assertEqual(report["rows"][0]["revenue"], 88.0)
        self.assertEqual(client.raw_calls[0]["params"]["filtering"], {"smart_plus_ad_ids": ["sp-ad-1"]})
