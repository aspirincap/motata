from __future__ import annotations

import unittest

from motata_cli.__main__ import build_parser
from motata_cli.meta import commands as meta_commands
from motata_cli.meta.user_type import build_user_type_report, classify_user_types, _scrape_content


class FakeMetaUserTypeClient:
    def get(self, path, *, params=None):
        if path == "me/adaccounts":
            return {"data": [{"id": "act_123", "account_id": "123", "name": "Demo Advertiser"}]}
        if path == "act_123":
            return {"id": "act_123", "account_id": "123", "name": "Demo Advertiser"}
        if path == "act_123/insights":
            return {"data": [{"spend": "500", "impressions": "10000", "clicks": "500"}]}
        if path == "cmp-shop":
            return {"id": "cmp-shop", "name": "Shopify sale campaign", "objective": "OUTCOME_SALES", "effective_status": "ACTIVE"}
        if path == "cmp-game":
            return {"id": "cmp-game", "name": "Puzzle casual game campaign", "objective": "OUTCOME_APP_PROMOTION", "effective_status": "ACTIVE"}
        if path == "ad-shop":
            return {
                "id": "ad-shop",
                "creative": {
                    "id": "creative-shop",
                    "object_story_spec": {
                        "link_data": {"link": "https://shop.example/products/running-shoes?utm_source=meta"}
                    },
                },
            }
        if path == "ad-game":
            return {"id": "ad-game", "creative": {"id": "creative-game"}}
        if path == "app-game":
            return {"id": "app-game", "name": "Tile Puzzle Fun", "category": "Games"}
        raise AssertionError(f"unexpected get path: {path}")

    def paginate(self, path, *, params=None):
        if path == "me/accounts":
            return []
        if path == "me/adaccounts":
            return [{"id": "act_123", "account_id": "123", "name": "Demo Advertiser"}]
        if path == "act_123/insights" and (params or {}).get("level") == "campaign":
            return [
                {
                    "campaign_id": "cmp-shop",
                    "campaign_name": "Shopify sale campaign",
                    "spend": "320",
                    "impressions": "6000",
                    "clicks": "300",
                },
                {
                    "campaign_id": "cmp-game",
                    "campaign_name": "Puzzle casual game campaign",
                    "spend": "180",
                    "impressions": "4000",
                    "clicks": "200",
                },
            ]
        if path == "act_123/insights":
            return [{"spend": "500", "impressions": "10000", "clicks": "500"}]
        if path == "cmp-shop/adsets":
            return []
        if path == "cmp-shop/ads":
            return []
        if path == "cmp-shop/insights":
            return [
                {
                    "account_id": "123",
                    "account_name": "Demo Advertiser",
                    "campaign_id": "cmp-shop",
                    "campaign_name": "Shopify sale campaign",
                    "ad_id": "ad-shop",
                    "ad_name": "shoe ad",
                    "spend": "320",
                    "impressions": "6000",
                    "reach": "5500",
                    "clicks": "300",
                    "inline_link_clicks": "250",
                }
            ]
        if path == "cmp-game/adsets":
            return [
                {
                    "id": "adset-game",
                    "campaign_id": "cmp-game",
                    "promoted_object": {
                        "application_id": "app-game",
                        "object_store_url": "https://play.google.com/store/apps/details?id=example.tile.puzzle",
                    },
                }
            ]
        if path == "cmp-game/insights":
            return [
                {
                    "account_id": "123",
                    "account_name": "Demo Advertiser",
                    "campaign_id": "cmp-game",
                    "campaign_name": "Puzzle casual game campaign",
                    "ad_id": "ad-game",
                    "ad_name": "puzzle ad",
                    "spend": "180",
                    "impressions": "4000",
                    "reach": "3600",
                    "clicks": "200",
                    "inline_link_clicks": "180",
                }
            ]
        raise AssertionError(f"unexpected paginate path: {path}")


class MetaUserTypeTests(unittest.TestCase):
    def test_user_type_analyze_command_is_exposed(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "meta",
                "user-type",
                "analyze",
                "--access-token",
                "demo-token",
                "--account-limit",
                "10",
                "--campaign-limit",
                "10",
            ]
        )

        self.assertIs(args.func, meta_commands.command_user_type_analyze)
        self.assertEqual(args.account_limit, 10)
        self.assertEqual(args.campaign_limit, 10)
        self.assertEqual(args.profile, "full")

    def test_classifier_returns_top_three_indices(self) -> None:
        result = classify_user_types(
            [
                {"text": "Shopify store checkout product sale", "source": "landing", "spend": 100, "ref": "https://shop.example/products/a"},
                {"text": "Tile puzzle casual game", "source": "store", "spend": 50, "ref": "https://play.google.com/store/apps/details?id=x"},
                {"text": "loan credit finance", "source": "landing", "spend": 20, "ref": "https://loan.example"},
            ]
        )

        self.assertEqual(len(result["top_types"]), 3)
        self.assertEqual(result["top_types"][0]["type"], "电商")
        self.assertEqual(result["top_types"][0]["index"], 100.0)

    def test_appstore_genre_casual_does_not_look_like_ecommerce_store(self) -> None:
        result = classify_user_types(
            [
                {
                    "text": (
                        "http://itunes.apple.com/app/id6744812012 "
                        "X-Clash: Survival Challenge primary_genre Games genres Games Casual Strategy "
                        "genre Games genre Casual genre Strategy appstore"
                    ),
                    "source": "scraped_store_url",
                    "spend": 1000,
                    "ref": "http://itunes.apple.com/app/id6744812012",
                }
            ]
        )

        self.assertEqual(result["top_types"][0]["type"], "休闲游戏")
        self.assertNotEqual(result["top_types"][0]["type"], "电商")

    def test_mahjong_app_name_classifies_as_casual_game(self) -> None:
        result = classify_user_types(
            [
                {
                    "text": "SolaMahjong install casual tile mobile app purchase",
                    "source": "account",
                    "spend": 1000,
                    "ref": "910178754916817",
                }
            ]
        )

        self.assertEqual(result["top_types"][0]["type"], "休闲游戏")

    def test_vietnamese_betting_landing_page_classifies_as_gambling(self) -> None:
        result = classify_user_types(
            [
                {
                    "text": "789K - NHÀ CÁI HÀNG ĐẦU CHÂU Á. Bạn có một lượt chơi miễn phí.",
                    "source": "scraped_landing_url",
                    "spend": 1000,
                    "ref": "https://789k.ad/?cid=4689924",
                }
            ]
        )

        self.assertEqual(result["top_types"][0]["type"], "赌博")
        self.assertGreater(result["top_types"][0]["raw_score"], 0)

    def test_gog_game_store_content_classifies_as_game_before_ecommerce(self) -> None:
        result = classify_user_types(
            [
                {
                    "text": "https://www.gog.com/en Welcome to GOG.com | best PC games DRM-free Store Owned Buy now RPG Action Adventure Strategy",
                    "source": "scraped_landing_url",
                    "spend": 100,
                    "ref": "https://www.gog.com/en",
                }
            ]
        )

        self.assertEqual(result["top_types"][0]["type"], "中重度游戏")

    def test_short_drama_w2a_url_classifies_as_short_drama_before_search_arbitrage(self) -> None:
        result = classify_user_types(
            [
                {
                    "text": (
                        "https://fb0917.sdtv02.top/app/87398.html?deep_link_value=shanhai://push?"
                        "link_id=87398&playletId=18044&type=1&af_dp=shanhai://push?playletId=18044&type=1 "
                        "I Don't Fight, I Expose the Emperor - StardustTV Continue watching "
                        "王妃 狼王 真千金 重生 豪门"
                    ),
                    "source": "scraped_landing_url",
                    "spend": 2761.67,
                    "ref": "https://fb0917.sdtv02.top/app/87398.html?deep_link_value=shanhai://push?link_id=87398&playletId=18044&type=1",
                }
            ]
        )

        self.assertEqual(result["top_types"][0]["type"], "短剧")
        top_types = {item["type"]: item["raw_score"] for item in result["top_types"]}
        self.assertGreater(top_types.get("短剧", 0), top_types.get("搜索套利", 0))

    def test_generic_find_word_has_low_search_arbitrage_weight(self) -> None:
        result = classify_user_types(
            [
                {
                    "text": (
                        "Top scholar Shattock investigates a case and they find the emperor is the mastermind. "
                        "Princess Cecilia and the prince consort finally unite."
                    ),
                    "source": "scraped_landing_url",
                    "spend": 1000,
                    "ref": "https://example.com/drama",
                }
            ]
        )

        self.assertEqual(result["top_types"][0]["type"], "搜索套利")
        self.assertLess(result["top_types"][0]["raw_score"], 2)

    def test_scrape_content_falls_back_to_generic_landing_page_text(self) -> None:
        def failing_product_scraper(url: str) -> dict:
            return {"error": "Unable to extract ecommerce product info"}

        rows = _scrape_content(
            [{"url": "https://landing.example", "spend": 100, "source": "landing_url"}],
            content_limit=1,
            product_scraper=failing_product_scraper,
            generic_scraper=lambda url: {
                "name": "789K - NHÀ CÁI HÀNG ĐẦU CHÂU Á",
                "description": "Bạn có một lượt chơi miễn phí.",
                "url_type": "landing_page",
            },
        )

        self.assertEqual(rows[0]["content"]["name"], "789K - NHÀ CÁI HÀNG ĐẦU CHÂU Á")
        self.assertIn("product_scrape_error", rows[0]["content"])

    def test_report_uses_top_campaign_urls_and_app_store_content(self) -> None:
        def fake_scraper(url: str) -> dict:
            if "play.google.com" in url:
                return {
                    "name": "Tile Puzzle Fun",
                    "description": "A casual puzzle and match tile game for everyone.",
                    "url_type": "googleplay",
                }
            return {
                "name": "Running Shoes",
                "description": "Shop product sale with discount and fast checkout.",
                "url_type": "ecommerce",
            }

        report = build_user_type_report(
            FakeMetaUserTypeClient(),
            account_limit=10,
            campaign_limit=10,
            ad_limit=5,
            content_limit=10,
            product_scraper=fake_scraper,
        )

        self.assertEqual(report["campaign_count"], 2)
        self.assertEqual(report["scraped_content_count"], 2)
        top_types = [item["type"] for item in report["top_types"]]
        self.assertIn("电商", top_types)
        self.assertIn("休闲游戏", top_types)
        shop_campaign = next(item for item in report["campaigns"] if item["campaign_id"] == "cmp-shop")
        self.assertEqual(shop_campaign["landing_urls"], ["https://shop.example/products/running-shoes"])

    def test_batch_profile_reduces_deep_user_type_probing(self) -> None:
        class BatchMetaUserTypeClient(FakeMetaUserTypeClient):
            def __init__(self) -> None:
                self.page_token_calls = 0

            def paginate(self, path, *, params=None):
                if path == "me/accounts":
                    self.page_token_calls += 1
                return super().paginate(path, params=params)

        meta = BatchMetaUserTypeClient()
        report = build_user_type_report(
            meta,
            account_limit=10,
            campaign_limit=10,
            ad_limit=5,
            content_limit=60,
            profile="batch",
            product_scraper=lambda url: {"name": "Demo", "description": "Shop product sale", "url_type": "ecommerce"},
        )

        self.assertEqual(meta.page_token_calls, 0)
        self.assertEqual(report["profile"], "batch")
        self.assertEqual(report["campaign_limit"], 5)
        self.assertEqual(report["ad_limit"], 2)
        self.assertEqual(report["content_limit"], 20)
        self.assertFalse(report["page_access_token_enabled"])


if __name__ == "__main__":
    unittest.main()
