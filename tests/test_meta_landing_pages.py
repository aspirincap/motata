from __future__ import annotations

import unittest

from motata_cli.__main__ import build_parser
from motata_cli.meta import commands as meta_commands
from motata_cli.meta.landing_pages import (
    build_landing_page_report,
    canonicalize_landing_url,
    discover_recent_spend_accounts,
    extract_landing_url,
)


class FakeMeta:
    def __init__(self) -> None:
        self.accounts = [{"id": "act_123", "account_id": "123", "name": "demo-account"}]
        self.insights = [
            {
                "account_id": "123",
                "account_name": "demo-account",
                "campaign_id": "cmp-1",
                "campaign_name": "campaign",
                "ad_id": "ad-1",
                "ad_name": "ad one",
                "spend": "10.00",
                "impressions": "100",
                "reach": "90",
                "clicks": "5",
                "inline_link_clicks": "4",
                "actions": [
                    {"action_type": "purchase", "value": "1"},
                    {"action_type": "omni_purchase", "value": "1"},
                    {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "1"},
                ],
                "purchase_roas": [{"action_type": "purchase", "value": "1.2"}],
            },
            {
                "account_id": "123",
                "account_name": "demo-account",
                "campaign_id": "cmp-1",
                "campaign_name": "campaign",
                "ad_id": "ad-2",
                "ad_name": "ad two",
                "spend": "5.00",
                "impressions": "50",
                "reach": "45",
                "clicks": "1",
                "inline_link_clicks": "1",
                "actions": [],
                "purchase_roas": [],
            },
        ]
        self.creatives = {
            "ad-1": {
                "creative": {
                    "id": "creative-1",
                    "object_story_spec": {
                        "video_data": {
                            "call_to_action": {
                                "value": {
                                    "link": "https://shop.example/products/demo-product?utm_source=meta&variant=1"
                                }
                            },
                            "image_url": "https://www.facebook.com/ads/image/?d=asset",
                        }
                    },
                }
            },
            "ad-2": {
                "creative": {
                    "id": "creative-2",
                    "object_story_spec": {
                        "link_data": {
                            "link": "https://shop.example/products/demo-product?utm_campaign=ignored"
                        }
                    },
                }
            },
        }

    def paginate(self, path: str, *, params: dict | None = None) -> list[dict]:
        if path == "me/accounts":
            return []
        if path == "me/adaccounts":
            return self.accounts
        if path == "act_123/insights":
            return self.insights
        raise AssertionError(f"Unexpected paginate path: {path}")

    def get(self, path: str, *, params: dict | None = None) -> dict:
        if path == "me/adaccounts":
            return {"data": self.accounts}
        if path == "act_123/insights":
            return {"data": [{"spend": "15.00", "impressions": "150", "clicks": "6"}]}
        if path in self.creatives:
            return self.creatives[path]
        raise AssertionError(f"Unexpected get path: {path}")


class MetaLandingPageParserTests(unittest.TestCase):
    def test_landing_pages_analyze_command_is_exposed(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "meta",
                "landing-pages",
                "analyze",
                "--access-token",
                "demo-token",
                "--since",
                "2026-04-24",
                "--until",
                "2026-05-07",
            ]
        )

        self.assertIs(args.func, meta_commands.command_landing_pages_analyze)
        self.assertEqual(args.since, "2026-04-24")
        self.assertEqual(args.until, "2026-05-07")
        self.assertEqual(args.account_limit, 10)
        self.assertEqual(args.profile, "full")
        self.assertTrue(args.no_product)

    def test_landing_pages_product_enrichment_is_explicit(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "meta",
                "landing-pages",
                "analyze",
                "--access-token",
                "demo-token",
                "--product",
            ]
        )

        self.assertFalse(args.no_product)


class MetaLandingPageExtractionTests(unittest.TestCase):
    def test_canonicalize_product_url_removes_tracking_query(self) -> None:
        self.assertEqual(
            canonicalize_landing_url("http://Shop.Example/products/demo?utm_source=meta&variant=1"),
            "https://shop.example/products/demo",
        )

    def test_extract_landing_url_prefers_cta_and_filters_meta_assets(self) -> None:
        url, source = extract_landing_url(
            {
                "object_story_spec": {
                    "video_data": {
                        "image_url": "https://www.facebook.com/ads/image/?d=asset",
                        "call_to_action": {"value": {"link": "https://shop.example/products/demo"}},
                    }
                }
            }
        )

        self.assertEqual(url, "https://shop.example/products/demo")
        self.assertEqual(source, "object_story_spec.video_data.call_to_action.value.link")


class MetaLandingPageReportTests(unittest.TestCase):
    def test_discovers_recent_spend_accounts_without_full_pagination(self) -> None:
        class DiscoveryMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.paginated_adaccounts = False
                self.accounts = [
                    {"id": "act_123", "account_id": "123", "name": "active"},
                    {"id": "act_456", "account_id": "456", "name": "inactive"},
                ]

            def paginate(self, path: str, *, params: dict | None = None) -> list[dict]:
                if path == "me/adaccounts":
                    self.paginated_adaccounts = True
                return super().paginate(path, params=params)

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "me/adaccounts":
                    return {"data": self.accounts}
                if path == "act_123/insights":
                    return {"data": [{"spend": "15.00", "impressions": "150", "clicks": "6"}]}
                if path == "act_456/insights":
                    return {"data": [{"spend": "0", "impressions": "0", "clicks": "0"}]}
                return super().get(path, params=params)

        meta = DiscoveryMeta()
        accounts, errors = discover_recent_spend_accounts(
            meta,
            since="2026-04-24",
            until="2026-05-07",
            account_limit=10,
        )

        self.assertEqual(errors, [])
        self.assertFalse(meta.paginated_adaccounts)
        self.assertEqual([account["account_id"] for account in accounts], ["123"])

    def test_report_groups_ads_by_product_url_and_dedupes_purchase_aliases(self) -> None:
        report = build_landing_page_report(
            FakeMeta(),
            since="2026-04-24",
            until="2026-05-07",
            enrich_product=True,
            product_scraper=lambda url: {"name": "Demo Product", "price": "19.99"},
        )

        self.assertEqual(report["ad_count"], 2)
        self.assertEqual(report["group_count"], 1)
        row = report["rows"][0]
        self.assertEqual(row["product_name"], "Demo Product")
        self.assertEqual(row["ad_count"], 2)
        self.assertEqual(row["spend"], 15.0)
        self.assertEqual(row["link_clicks"], 5)
        self.assertEqual(row["purchases"], 1.0)
        self.assertEqual(row["cpp"], 15.0)
        self.assertEqual(row["url"], "https://shop.example/products/demo-product")
        self.assertEqual(row["top_ads"][0]["creative_id"], "creative-1")
        self.assertEqual(row["top_ads"][0]["preview_image_url"], "https://www.facebook.com/ads/image/?d=asset")
        self.assertEqual(row["top_ads"][0]["preview_url"], "https://www.facebook.com/ads/image/?d=asset")

    def test_report_bulk_fetches_ad_contexts(self) -> None:
        class BulkContextMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.bulk_context_calls = 0
                self.single_ad_context_calls = 0

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "":
                    self.bulk_context_calls += 1
                    ids = str((params or {}).get("ids") or "").split(",")
                    return {ad_id: self.creatives[ad_id] for ad_id in ids if ad_id in self.creatives}
                if path in {"ad-1", "ad-2"}:
                    self.single_ad_context_calls += 1
                return super().get(path, params=params)

        meta = BulkContextMeta()
        report = build_landing_page_report(
            meta,
            since="2026-04-24",
            until="2026-05-07",
        )

        self.assertEqual(meta.bulk_context_calls, 1)
        self.assertEqual(meta.single_ad_context_calls, 0)
        self.assertEqual(report["rows"][0]["ad_count"], 2)

    def test_batch_profile_skips_deep_ad_context_fetches(self) -> None:
        class BatchMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.bulk_context_calls = 0

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "":
                    self.bulk_context_calls += 1
                    raise AssertionError("batch profile should not bulk fetch ad contexts")
                return super().get(path, params=params)

        meta = BatchMeta()
        report = build_landing_page_report(
            meta,
            since="2026-04-24",
            until="2026-05-07",
            profile="batch",
        )

        self.assertEqual(meta.bulk_context_calls, 0)
        self.assertEqual(report["profile"], "batch")
        self.assertEqual(report["max_ad_context_fetches"], 0)
        self.assertEqual(report["batch_skipped_ad_context_fetch_count"], 2)
        self.assertEqual(report["group_count"], 0)

    def test_report_uses_insight_url_breakdown_before_ad_context(self) -> None:
        class InsightUrlMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.bulk_context_ids = []

            def paginate(self, path: str, *, params: dict | None = None) -> list[dict]:
                if path == "act_123/insights" and (params or {}).get("breakdowns") == "link_url_asset":
                    return [
                        {
                            "account_id": "123",
                            "account_name": "demo-account",
                            "campaign_id": "cmp-1",
                            "campaign_name": "campaign",
                            "ad_id": "ad-1",
                            "ad_name": "ad one",
                            "spend": "10.00",
                            "link_url_asset": "https://shop.example/products/demo-product?utm_source=meta",
                        }
                    ]
                if path == "act_123/insights" and (params or {}).get("breakdowns"):
                    return []
                return super().paginate(path, params=params)

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "":
                    ids = str((params or {}).get("ids") or "").split(",")
                    self.bulk_context_ids.extend(ids)
                    return {ad_id: self.creatives[ad_id] for ad_id in ids if ad_id in self.creatives}
                return super().get(path, params=params)

        meta = InsightUrlMeta()
        report = build_landing_page_report(
            meta,
            since="2026-04-24",
            until="2026-05-07",
        )

        self.assertEqual(report["insight_direct_url_count"], 1)
        self.assertEqual(report["skipped_ad_context_fetch_count"], 0)
        self.assertEqual(report["preview_ad_context_fetch_count"], 1)
        self.assertEqual(meta.bulk_context_ids, ["ad-2", "ad-1"])
        self.assertEqual(report["rows"][0]["url"], "https://shop.example/products/demo-product")

    def test_preview_context_fetches_only_ads_in_final_report(self) -> None:
        class FinalOnlyPreviewMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.bulk_context_ids = []
                self.insights = [
                    {
                        "account_id": "123",
                        "account_name": "demo-account",
                        "campaign_id": "cmp-keep",
                        "campaign_name": "keep campaign",
                        "ad_id": "ad-keep",
                        "ad_name": "keep ad",
                        "spend": "20.00",
                        "impressions": "200",
                        "reach": "180",
                        "clicks": "10",
                        "inline_link_clicks": "8",
                        "actions": [],
                        "purchase_roas": [],
                    },
                    {
                        "account_id": "123",
                        "account_name": "demo-account",
                        "campaign_id": "cmp-drop",
                        "campaign_name": "drop campaign",
                        "ad_id": "ad-drop",
                        "ad_name": "drop ad",
                        "spend": "5.00",
                        "impressions": "50",
                        "reach": "45",
                        "clicks": "2",
                        "inline_link_clicks": "1",
                        "actions": [],
                        "purchase_roas": [],
                    },
                ]
                self.creatives = {
                    "ad-keep": {
                        "id": "ad-keep",
                        "creative": {
                            "id": "creative-keep",
                            "thumbnail_url": "https://scontent.example/keep.jpg",
                        },
                    },
                    "ad-drop": {
                        "id": "ad-drop",
                        "creative": {
                            "id": "creative-drop",
                            "thumbnail_url": "https://scontent.example/drop.jpg",
                        },
                    },
                }

            def paginate(self, path: str, *, params: dict | None = None) -> list[dict]:
                if path == "act_123/insights" and (params or {}).get("breakdowns") == "link_url_asset":
                    return [
                        {
                            **self.insights[0],
                            "link_url_asset": "https://shop.example/products/keep",
                        },
                        {
                            **self.insights[1],
                            "link_url_asset": "https://shop.example/products/drop",
                        },
                    ]
                if path == "act_123/insights" and (params or {}).get("breakdowns"):
                    return []
                return super().paginate(path, params=params)

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "":
                    ids = str((params or {}).get("ids") or "").split(",")
                    self.bulk_context_ids.extend(ids)
                    return {ad_id: self.creatives[ad_id] for ad_id in ids if ad_id in self.creatives}
                return super().get(path, params=params)

        meta = FinalOnlyPreviewMeta()
        report = build_landing_page_report(
            meta,
            since="2026-04-24",
            until="2026-05-07",
            top=1,
        )

        self.assertEqual([row["url"] for row in report["rows"]], ["https://shop.example/products/keep"])
        self.assertEqual(meta.bulk_context_ids, ["ad-keep"])
        self.assertEqual(report["preview_ad_context_fetch_count"], 1)
        self.assertEqual(report["rows"][0]["top_ads"][0]["preview_image_url"], "https://scontent.example/keep.jpg")

    def test_report_falls_back_to_story_attachment_url(self) -> None:
        class StoryFallbackMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.insights = [
                    {
                        "account_id": "123",
                        "account_name": "demo-account",
                        "campaign_id": "cmp-2",
                        "campaign_name": "story campaign",
                        "ad_id": "ad-story",
                        "ad_name": "story ad",
                        "spend": "7.00",
                        "impressions": "70",
                        "reach": "65",
                        "clicks": "3",
                        "inline_link_clicks": "2",
                        "actions": [],
                        "purchase_roas": [],
                    }
                ]
                self.creatives = {
                    "ad-story": {
                        "id": "ad-story",
                        "creative": {
                            "id": "creative-story",
                            "object_story_id": "page-1_post-1",
                            "effective_object_story_id": "page-1_post-1",
                            "thumbnail_url": "https://scontent.example/thumb.jpg",
                        },
                    },
                    "page-1_post-1": {
                        "id": "page-1_post-1",
                        "attachments": {
                            "data": [
                                {
                                    "unshimmed_url": "https://shop.example/products/story-demo?utm_source=meta",
                                    "url": "https://www.facebook.com/story.php?id=page-1&story_fbid=post-1",
                                }
                            ]
                        },
                    },
                }

            def paginate(self, path: str, *, params: dict | None = None) -> list[dict]:
                if path == "me/accounts":
                    return [{"id": "page-1", "name": "Story Page", "access_token": "page-1-token"}]
                return super().paginate(path, params=params)

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "page-1_post-1" and (params or {}).get("access_token") != "page-1-token":
                    raise RuntimeError("missing page token")
                return super().get(path, params=params)

        report = build_landing_page_report(
            StoryFallbackMeta(),
            since="2026-04-24",
            until="2026-05-07",
        )

        self.assertEqual(report["no_url_count"], 0)
        self.assertEqual(report["rows"][0]["url"], "https://shop.example/products/story-demo")
        self.assertEqual(report["rows"][0]["url_source"], "story.attachments.data[0].unshimmed_url")

    def test_report_uses_page_token_for_story_attachment_url(self) -> None:
        class PageTokenMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.insights = [
                    {
                        "account_id": "123",
                        "account_name": "demo-account",
                        "campaign_id": "cmp-4",
                        "campaign_name": "page token campaign",
                        "ad_id": "ad-page-token",
                        "ad_name": "page token ad",
                        "spend": "11.00",
                        "impressions": "110",
                        "reach": "100",
                        "clicks": "4",
                        "inline_link_clicks": "3",
                        "actions": [],
                        "purchase_roas": [],
                    }
                ]
                self.creatives = {
                    "ad-page-token": {
                        "id": "ad-page-token",
                        "creative": {
                            "id": "creative-page-token",
                            "object_story_id": "page-3_post-3",
                            "effective_object_story_id": "page-3_post-3",
                            "thumbnail_url": "https://scontent.example/thumb.jpg",
                        },
                    },
                    "page-3_post-3": {
                        "id": "page-3_post-3",
                        "call_to_action": {
                            "value": {
                                "link": "https://shop.example/products/page-token-demo?utm_source=meta"
                            }
                        },
                    },
                }

            def paginate(self, path: str, *, params: dict | None = None) -> list[dict]:
                if path == "me/accounts":
                    return [{"id": "page-3", "name": "Demo Page", "access_token": "page-token"}]
                return super().paginate(path, params=params)

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "page-3_post-3" and (params or {}).get("access_token") != "page-token":
                    raise RuntimeError("missing page token")
                return super().get(path, params=params)

        report = build_landing_page_report(
            PageTokenMeta(),
            since="2026-04-24",
            until="2026-05-07",
        )

        self.assertEqual(report["page_access_token_count"], 1)
        self.assertEqual(report["no_url_count"], 0)
        self.assertEqual(report["rows"][0]["url"], "https://shop.example/products/page-token-demo")

    def test_report_caches_story_lookup_for_same_page_post(self) -> None:
        class CachedStoryMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.story_get_count = 0
                self.insights = [
                    {
                        "account_id": "123",
                        "account_name": "demo-account",
                        "campaign_id": "cmp-5",
                        "campaign_name": "cached story campaign",
                        "ad_id": "ad-cached-1",
                        "ad_name": "cached ad one",
                        "spend": "4.00",
                        "impressions": "40",
                        "reach": "35",
                        "clicks": "2",
                        "inline_link_clicks": "2",
                        "actions": [],
                        "purchase_roas": [],
                    },
                    {
                        "account_id": "123",
                        "account_name": "demo-account",
                        "campaign_id": "cmp-5",
                        "campaign_name": "cached story campaign",
                        "ad_id": "ad-cached-2",
                        "ad_name": "cached ad two",
                        "spend": "6.00",
                        "impressions": "60",
                        "reach": "55",
                        "clicks": "3",
                        "inline_link_clicks": "3",
                        "actions": [],
                        "purchase_roas": [],
                    },
                ]
                self.creatives = {
                    "ad-cached-1": {
                        "id": "ad-cached-1",
                        "creative": {
                            "id": "creative-cached-1",
                            "object_story_id": "page-4_post-4",
                            "effective_object_story_id": "page-4_post-4",
                        },
                    },
                    "ad-cached-2": {
                        "id": "ad-cached-2",
                        "creative": {
                            "id": "creative-cached-2",
                            "object_story_id": "page-4_post-4",
                            "effective_object_story_id": "page-4_post-4",
                        },
                    },
                    "page-4_post-4": {
                        "id": "page-4_post-4",
                        "call_to_action": {
                            "value": {"link": "https://shop.example/products/cached-story"}
                        },
                    },
                }

            def paginate(self, path: str, *, params: dict | None = None) -> list[dict]:
                if path == "me/accounts":
                    return [{"id": "page-4", "name": "Cached Page", "access_token": "page-4-token"}]
                return super().paginate(path, params=params)

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "page-4_post-4":
                    if (params or {}).get("access_token") != "page-4-token":
                        raise RuntimeError("missing page token")
                    self.story_get_count += 1
                return super().get(path, params=params)

        meta = CachedStoryMeta()
        report = build_landing_page_report(
            meta,
            since="2026-04-24",
            until="2026-05-07",
        )

        self.assertEqual(meta.story_get_count, 1)
        self.assertEqual(report["story_probe_count"], 1)
        self.assertEqual(report["rows"][0]["ad_count"], 2)
        self.assertEqual(report["rows"][0]["spend"], 10.0)

    def test_report_includes_structure_for_unresolved_story_ads(self) -> None:
        class UnresolvedStoryMeta(FakeMeta):
            def __init__(self) -> None:
                super().__init__()
                self.insights = [
                    {
                        "account_id": "123",
                        "account_name": "demo-account",
                        "campaign_id": "cmp-3",
                        "campaign_name": "unresolved campaign",
                        "ad_id": "ad-unresolved",
                        "ad_name": "unresolved ad",
                        "spend": "3.00",
                        "impressions": "30",
                        "reach": "25",
                        "clicks": "1",
                        "inline_link_clicks": "1",
                        "actions": [],
                        "purchase_roas": [],
                    }
                ]
                self.creatives = {
                    "ad-unresolved": {
                        "id": "ad-unresolved",
                        "creative": {
                            "id": "creative-unresolved",
                            "object_story_id": "page-2_post-2",
                            "effective_object_story_id": "page-2_post-2",
                            "thumbnail_url": "https://scontent.example/thumb.jpg",
                        },
                    }
                }

            def get(self, path: str, *, params: dict | None = None) -> dict:
                if path == "page-2_post-2":
                    raise RuntimeError("missing page permission")
                return super().get(path, params=params)

        report = build_landing_page_report(
            UnresolvedStoryMeta(),
            since="2026-04-24",
            until="2026-05-07",
        )

        self.assertEqual(report["no_url_count"], 1)
        unresolved = report["no_url"][0]
        self.assertEqual(unresolved["creative_shape"], "story")
        self.assertEqual(unresolved["preview_image_url"], "https://scontent.example/thumb.jpg")
        self.assertEqual(unresolved["preview_url"], "https://www.facebook.com/page-2_post-2")
        self.assertEqual(unresolved["facebook_post"], "Facebook.com/page-2_post-2")
        self.assertEqual(unresolved["story_probe_error"], "story lookup requires page access token from /me/accounts")


if __name__ == "__main__":
    unittest.main()
