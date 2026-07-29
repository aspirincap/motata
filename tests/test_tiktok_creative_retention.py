from __future__ import annotations

import json
import unittest

from motata_cli.__main__ import build_parser
from motata_cli.metrics.core import build_core_metric_coverage
from motata_cli.tiktok import commands as tiktok_commands
from motata_cli.tiktok.creative_retention import _tt_video_payload_by_item_id, build_tiktok_creative_retention_report


class FakeTikTokCreativeRetentionClient:
    def __init__(self, *, probe_revenue: bool = True) -> None:
        self.probe_revenue = probe_revenue
        self.calls: list[tuple[str, tuple[str, ...], str]] = []
        self.report_kwargs: list[dict] = []
        self.list_ads_calls: list[dict] = []
        self.video_info_calls: list[list[str]] = []
        self.image_info_calls: list[list[str]] = []
        self.tt_video_calls: list[dict] = []

    def integrated_report(self, report_type: str, **kwargs):
        metrics = tuple(kwargs.get("metrics") or [])
        data_level = str(kwargs.get("data_level") or "")
        self.calls.append((report_type, metrics, data_level))
        self.report_kwargs.append(kwargs)
        if report_type != "BASIC":
            raise AssertionError(f"unexpected report_type: {report_type}")

        dimensions = kwargs.get("dimensions") or []
        if data_level == "AUCTION_AD":
            rows = [
                self._row(
                    dimensions,
                    ["ad-good"],
                    ad_name="episode good",
                    ad_url="https://report.example/products/good?utm_source=tiktok",
                    spend="100",
                    impressions="10000",
                    clicks="500",
                    conversion="50",
                    video_play_actions="8000",
                    video_watched_2s="6500",
                    video_watched_6s="4000",
                    engaged_view="3800",
                    engaged_view_15s="1000",
                    video_views_p25="6000",
                    video_views_p50="4000",
                    video_views_p75="2500",
                    video_views_p100="1600",
                ),
                self._row(
                    dimensions,
                    ["ad-bad"],
                    ad_name="episode bad",
                    spend="300",
                    impressions="20000",
                    clicks="3000",
                    conversion="5",
                    video_play_actions="12000",
                    video_watched_2s="5000",
                    video_watched_6s="1200",
                    engaged_view="1000",
                    engaged_view_15s="100",
                    video_views_p25="3000",
                    video_views_p50="600",
                    video_views_p75="200",
                    video_views_p100="50",
                ),
                self._row(
                    dimensions,
                    ["ad-spark"],
                    ad_name="spark only",
                    spend="80",
                    impressions="8000",
                    clicks="200",
                    conversion="8",
                    video_play_actions="0",
                    video_watched_2s="0",
                    video_watched_6s="0",
                    engaged_view="0",
                    engaged_view_15s="0",
                    video_views_p25="0",
                    video_views_p50="0",
                    video_views_p75="0",
                    video_views_p100="0",
                ),
            ]
            filters = kwargs.get("filtering") or []
            requested_ids = set()
            for item in filters:
                if item.get("field_name") == "ad_ids":
                    raw_value = item.get("filter_value") or []
                    if isinstance(raw_value, str):
                        raw_value = json.loads(raw_value)
                    requested_ids.update(str(value) for value in raw_value)
            if requested_ids:
                rows = [row for row in rows if row["dimensions"].get("ad_id") in requested_ids]
            return {"data": {"list": rows}}

        row_metrics = {metric: "0" for metric in metrics}
        row_metrics.update({"spend": "400", "impressions": "30000", "clicks": "3500", "conversion": "55"})
        if self.probe_revenue:
            row_metrics["total_purchase_value"] = "88"
        return {"data": {"list": [{"dimensions": {dimension: "123" for dimension in dimensions}, "metrics": row_metrics}]}}

    def list_ads(self, advertiser_id: str, **kwargs):
        self.list_ads_calls.append(kwargs)
        requested_ids = set((kwargs.get("filtering") or {}).get("ad_ids") or [])
        ads = [
            {
                "ad_id": "ad-good",
                "ad_name": "episode good",
                "video_id": "vid-good",
            },
            {
                "ad_id": "ad-bad",
                "ad_name": "episode bad",
                "landing_page_url": "https://detail.example/products/bad?utm_source=tiktok",
                "image_ids": ["img-bad"],
            },
            {
                "ad_id": "ad-spark",
                "ad_name": "spark only",
                "tiktok_item_id": "1234567891234567891",
                "identity_type": "AUTH_CODE",
            },
        ]
        return {"data": {"list": [ad for ad in ads if ad["ad_id"] in requested_ids]}}

    def get_video_info(self, advertiser_id: str, video_ids: list[str]):
        self.video_info_calls.append(video_ids)
        videos = [
            {
                "video_id": "vid-good",
                "video_cover_url": "https://p16-sign-va.tiktokcdn.com/cover.jpg",
                "video_url": "https://p16-sign-va.tiktokcdn.com/video.mp4",
            }
        ]
        return {"data": {"list": [video for video in videos if video["video_id"] in set(video_ids)]}}

    def get_image_info(self, advertiser_id: str, image_ids: list[str]):
        self.image_info_calls.append(image_ids)
        images = [
            {
                "image_id": "img-bad",
                "image_url": "https://p16-sign-va.tiktokcdn.com/static.jpg",
            }
        ]
        return {"data": {"list": [image for image in images if image["image_id"] in set(image_ids)]}}

    def list_tt_videos(self, advertiser_id: str, **kwargs):
        self.tt_video_calls.append(kwargs)
        if kwargs.get("keyword") != "1234567891234567891":
            return {"data": {"list": []}}
        return {
            "data": {
                "list": [
                    {
                        "item_info": {"item_id": "1234567891234567891", "item_type": "VIDEO"},
                        "video_info": {
                            "poster_url": "https://p16-sign-va.tiktokcdn.com/spark-cover.jpg",
                            "preview_url": "https://p16-sign-va.tiktokcdn.com/spark-preview.mp4",
                        },
                    }
                ]
            }
        }

    @staticmethod
    def _row(dimensions: list[str], values: list[str], **metrics):
        payload = {
            "dimensions": dict(zip(dimensions, values, strict=True)),
            "metrics": dict(metrics),
        }
        for key in ("ad_name", "campaign_name", "adgroup_name"):
            if key in metrics:
                payload["metrics"][key] = metrics[key]
        return payload


class FakeTikTokVideoListClient:
    def __init__(self) -> None:
        self.tt_video_calls: list[dict] = []

    def list_tt_videos(self, advertiser_id: str, **kwargs):
        self.tt_video_calls.append(kwargs)
        if kwargs.get("keyword"):
            return {"data": {"list": []}}
        page = int(kwargs.get("page") or 1)
        pages = {
            1: [
                {"item_info": {"item_id": "1111111111111111111", "item_type": "VIDEO"}},
                {"item_info": {"item_id": "2222222222222222222", "item_type": "VIDEO"}},
            ],
            2: [
                {"item_info": {"item_id": "3333333333333333333", "item_type": "CAROUSEL"}},
            ],
        }
        return {
            "data": {
                "list": pages.get(page, []),
                "page_info": {"page": page, "page_size": kwargs.get("page_size"), "total_page": 2},
            }
        }


class FakeUpgradedSmartPlusRetentionClient(FakeTikTokCreativeRetentionClient):
    def __init__(self) -> None:
        super().__init__(probe_revenue=True)
        self.raw_calls: list[dict] = []

    def integrated_report(self, report_type: str, **kwargs):
        metrics = tuple(kwargs.get("metrics") or [])
        data_level = str(kwargs.get("data_level") or "")
        self.calls.append((report_type, metrics, data_level))
        self.report_kwargs.append(kwargs)
        if data_level != "AUCTION_AD":
            return {"data": {"list": []}}
        return {
            "data": {
                "list": [
                    self._row(
                        kwargs.get("dimensions") or [],
                        ["ad-upgraded-creative"],
                        ad_name="upgraded creative",
                        spend="50",
                        impressions="5000",
                        clicks="100",
                        conversion="10",
                        video_play_actions="3000",
                        video_watched_2s="2000",
                        video_watched_6s="1200",
                        engaged_view="1100",
                        engaged_view_15s="500",
                        video_views_p25="1800",
                        video_views_p50="1200",
                        video_views_p75="800",
                        video_views_p100="400",
                    )
                ]
            }
        }

    def list_ads(self, advertiser_id: str, **kwargs):
        self.list_ads_calls.append(kwargs)
        requested_ids = set((kwargs.get("filtering") or {}).get("ad_ids") or [])
        if "ad-upgraded-creative" not in requested_ids:
            return {"data": {"list": []}}
        return {
            "data": {
                "list": [
                    {
                        "ad_id": "ad-upgraded-creative",
                        "campaign_automation_type": "UPGRADED_SMART_PLUS_CREATIVE",
                        "smart_plus_ad_id": "sp-upgraded-ad",
                        "campaign_id": "cmp-upgraded",
                        "adgroup_id": "ag-upgraded",
                        "video_id": "vid-good",
                    }
                ]
            }
        }

    def _raw_request(self, method, path, *, params=None, json_body=None, timeout=60):
        self.raw_calls.append({"method": method, "path": path, "params": params, "timeout": timeout})
        filtering = (params or {}).get("filtering") or {}
        requested_smart_plus_ids = set(filtering.get("smart_plus_ad_ids") or [])
        if path == "smart_plus/ad/get/" and "sp-upgraded-ad" in requested_smart_plus_ids:
            return {
                "data": {
                    "list": [
                        {
                            "smart_plus_ad_id": "sp-upgraded-ad",
                            "landing_page_url_list": [
                                {"landing_page_url": "https://smart.example/products/upgraded?utm_source=tiktok"}
                            ],
                            "creative_list": [{"video_url": "https://p16-sign-va.tiktokcdn.com/upgraded.mp4"}],
                        }
                    ]
                }
            }
        return {"data": {"list": []}}


class TikTokCreativeRetentionTests(unittest.TestCase):
    def test_core_metric_coverage_requires_revenue(self) -> None:
        coverage = build_core_metric_coverage(
            platform="tiktok",
            totals={"spend": 10, "impressions": 100, "clicks": 3, "conversion": 1, "revenue": 0},
        )

        self.assertFalse(coverage["complete"])
        self.assertIn("revenue", coverage["missing_core_metrics"])
        self.assertTrue(coverage["needs_full_probe"])

    def test_tiktok_creative_retention_command_is_exposed(self) -> None:
        args = build_parser().parse_args(
            [
                "tiktok",
                "creative-retention",
                "report",
                "--advertiser-id",
                "123",
                "--access-token",
                "demo-token",
                "--since",
                "2026-05-01",
                "--until",
                "2026-05-09",
            ]
        )

        self.assertIs(args.func, tiktok_commands.command_tiktok_creative_retention_report)
        self.assertEqual(args.advertiser_id, "123")

    def test_retention_report_accepts_target_ad_ids(self) -> None:
        client = FakeTikTokCreativeRetentionClient(probe_revenue=True)
        result = build_tiktok_creative_retention_report(
            client,
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
            target_ad_ids=["ad-good"],
        )

        self.assertEqual(result["scope"], "targeted_ad_ids")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["sections"]["top_conversion_creatives"][0]["ad_id"], "ad-good")
        self.assertEqual(result["request_stats"]["target_ad_id_count"], 1)

    def test_retention_report_chunks_target_ad_ids_at_20(self) -> None:
        client = FakeTikTokCreativeRetentionClient(probe_revenue=True)
        ids = ["ad-good", "ad-bad", "ad-spark", *[f"ad-extra-{idx}" for idx in range(22)]]
        result = build_tiktok_creative_retention_report(
            client,
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
            target_ad_ids=ids,
        )

        ad_report_calls = [
            kwargs
            for kwargs in client.report_kwargs
            if kwargs.get("data_level") == "AUCTION_AD" and kwargs.get("filtering")
        ]
        filter_sizes = []
        for kwargs in ad_report_calls:
            raw_value = (kwargs.get("filtering") or [{}])[0].get("filter_value")
            filter_sizes.append(len(json.loads(raw_value)))
        self.assertEqual(filter_sizes, [20, 5])
        self.assertEqual(result["request_stats"]["target_ad_id_count"], 25)

    def test_retention_report_uses_tt_video_list_for_spark_item_id(self) -> None:
        client = FakeTikTokCreativeRetentionClient(probe_revenue=True)
        result = build_tiktok_creative_retention_report(
            client,
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
            target_ad_ids=["ad-spark"],
            top=1,
        )

        row = result["sections"]["top_conversion_creatives"][0]
        self.assertEqual(row["ad_id"], "ad-spark")
        self.assertEqual(row["preview_image_url"], "https://p16-sign-va.tiktokcdn.com/spark-cover.jpg")
        self.assertEqual(row["preview_url"], "https://p16-sign-va.tiktokcdn.com/spark-preview.mp4")
        self.assertEqual(result["request_stats"]["preview_enrichment"]["tt_video_item_count"], 1)

    def test_retention_report_carries_landing_url_from_report_and_detail(self) -> None:
        client = FakeTikTokCreativeRetentionClient(probe_revenue=True)
        result = build_tiktok_creative_retention_report(
            client,
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
            target_ad_ids=["ad-good", "ad-bad"],
            top=2,
        )

        rows = {row["ad_id"]: row for row in result["sections"]["top_conversion_creatives"]}
        self.assertEqual(rows["ad-good"]["landing_url"], "https://report.example/products/good")
        self.assertEqual(rows["ad-good"]["landing_url_source"], "metrics.ad_url")
        self.assertEqual(rows["ad-bad"]["landing_url"], "https://detail.example/products/bad")
        self.assertEqual(rows["ad-bad"]["landing_url_source"], "landing_page_url")

    def test_retention_preview_reuses_cached_ad_detail(self) -> None:
        client = FakeTikTokCreativeRetentionClient(probe_revenue=True)
        result = build_tiktok_creative_retention_report(
            client,
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
            target_ad_ids=["ad-good"],
            top=1,
            ad_details_by_id={
                "ad-good": {
                    "ad_id": "ad-good",
                    "ad_name": "episode good",
                    "video_id": "vid-good",
                    "landing_page_url": "https://cached.example/products/good",
                }
            },
        )

        self.assertEqual(client.list_ads_calls, [])
        preview_stats = result["request_stats"]["preview_enrichment"]
        self.assertEqual(preview_stats["cached_detail_count"], 1)
        self.assertEqual(preview_stats["detail_fetch_count"], 0)
        row = result["sections"]["top_conversion_creatives"][0]
        self.assertEqual(row["preview_url"], "https://p16-sign-va.tiktokcdn.com/video.mp4")
        self.assertEqual(row["landing_url"], "https://cached.example/products/good")

    def test_retention_uses_smart_plus_ad_get_for_upgraded_landing_url(self) -> None:
        client = FakeUpgradedSmartPlusRetentionClient()

        result = build_tiktok_creative_retention_report(
            client,
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
            target_ad_ids=["ad-upgraded-creative"],
            top=1,
        )

        row = result["sections"]["top_conversion_creatives"][0]
        self.assertEqual(row["ad_id"], "ad-upgraded-creative")
        self.assertEqual(row["ad_id_v2"], "sp-upgraded-ad")
        self.assertEqual(row["landing_url"], "https://smart.example/products/upgraded")
        self.assertEqual(row["landing_url_source"], "landing_page_url_list[0].landing_page_url")
        self.assertEqual(client.raw_calls[0]["path"], "smart_plus/ad/get/")
        self.assertEqual(client.raw_calls[0]["params"]["filtering"]["smart_plus_ad_ids"][0], "sp-upgraded-ad")

    def test_tt_video_list_bulk_indexes_account_items_for_multiple_ids(self) -> None:
        client = FakeTikTokVideoListClient()
        result = _tt_video_payload_by_item_id(
            client,
            "123",
            ["2222222222222222222", "3333333333333333333"],
        )

        self.assertEqual(set(result), {"2222222222222222222", "3333333333333333333"})
        self.assertEqual([call.get("keyword") for call in client.tt_video_calls], [None, None])
        self.assertEqual([call.get("page") for call in client.tt_video_calls], [1, 2])

    def test_retention_report_ranks_and_uses_probe_for_missing_revenue(self) -> None:
        client = FakeTikTokCreativeRetentionClient(probe_revenue=True)
        result = build_tiktok_creative_retention_report(
            client,
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
            top=1,
            page_size=10,
            max_pages=1,
        )

        self.assertEqual(result["sections"]["top_retention_creatives"][0]["ad_id"], "ad-good")
        self.assertEqual(
            result["sections"]["top_retention_creatives"][0]["preview_image_url"],
            "https://p16-sign-va.tiktokcdn.com/cover.jpg",
        )
        self.assertEqual(
            result["sections"]["top_retention_creatives"][0]["preview_url"],
            "https://p16-sign-va.tiktokcdn.com/video.mp4",
        )
        self.assertEqual(result["sections"]["high_spend_low_retention"][0]["ad_id"], "ad-bad")
        self.assertEqual(
            result["sections"]["high_spend_low_retention"][0]["preview_image_url"],
            "https://p16-sign-va.tiktokcdn.com/static.jpg",
        )
        self.assertEqual(
            result["request_stats"]["preview_enrichment"]["available_count"],
            3,
        )
        self.assertTrue(result["supplemental_probe"]["triggered"])
        self.assertTrue(result["core_metric_coverage"]["complete"])

    def test_retention_report_keeps_revenue_gap_when_probe_empty(self) -> None:
        result = build_tiktok_creative_retention_report(
            FakeTikTokCreativeRetentionClient(probe_revenue=False),
            advertiser_id="123",
            start_date="2026-05-01",
            end_date="2026-05-09",
            top=1,
            page_size=10,
            max_pages=1,
        )

        self.assertIn("revenue", result["core_metric_coverage"]["missing_core_metrics"])
        self.assertIn("Revenue remains missing", " ".join(result["analysis_notes"]))


if __name__ == "__main__":
    unittest.main()
