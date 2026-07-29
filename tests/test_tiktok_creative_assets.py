from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from motata_cli.__main__ import build_parser
from motata_cli.tiktok import commands


class FakeCreativeClient:
    def __init__(self, pages: list[list[dict[str, object]]]):
        self.pages = pages
        self.calls: list[dict[str, object]] = []
        self.campaign = {
            "campaign_id": "c1",
            "campaign_name": "Summer Sale Campaign",
            "objective_type": "APP_PROMOTION",
            "app_promotion_type": "APP_INSTALL",
        }

    def list_creative_portfolios(self, advertiser_id: str, *, filtering=None, page=None, page_size=None):
        self.calls.append(
            {
                "advertiser_id": advertiser_id,
                "filtering": filtering,
                "page": page,
                "page_size": page_size,
            }
        )
        page_index = (page or 1) - 1
        items = self.pages[page_index] if 0 <= page_index < len(self.pages) else []
        return {
            "data": {
                "creative_portfolios": items,
                "page_info": {"total_page": len(self.pages)},
            }
        }

    def get_creative_portfolio(self, advertiser_id: str, creative_portfolio_id: str):
        for page in self.pages:
            for item in page:
                if str(item.get("creative_portfolio_id")) == str(creative_portfolio_id):
                    detail = dict(item)
                    detail["creative_portfolio_id"] = creative_portfolio_id
                    detail["portfolio_content"] = item.get("creative_portfolio_content")
                    return {"data": detail}
        raise commands.CliError(f"TikTok creative portfolio not found: {creative_portfolio_id}")

    def get_campaign(self, advertiser_id: str, campaign_id: str, *, smart_plus: bool = False):
        detail = dict(self.campaign)
        detail["campaign_id"] = campaign_id
        return detail

    def list_adgroups(self, advertiser_id: str, *, filtering=None, page=None, page_size=None, smart_plus: bool = False, **kwargs):
        self.calls.append(
            {
                "method": "list_adgroups",
                "advertiser_id": advertiser_id,
                "filtering": filtering,
                "page": page,
                "page_size": page_size,
                "smart_plus": smart_plus,
            }
        )
        return {
            "data": {
                "list": [
                    {
                        "adgroup_id": "g1",
                        "campaign_id": "c1",
                        "app_id": "app1",
                        "promotion_type": "APP_ANDROID",
                        "optimization_goal": "INSTALL",
                    }
                ],
                "page_info": {"total_page": 1},
            }
        }

    def list_ads(self, advertiser_id: str, *, filtering=None, page=None, page_size=None, smart_plus: bool = False, **kwargs):
        self.calls.append(
            {
                "method": "list_ads",
                "advertiser_id": advertiser_id,
                "filtering": filtering,
                "page": page,
                "page_size": page_size,
                "smart_plus": smart_plus,
            }
        )
        return {
            "data": {
                "list": [
                    {
                        "smart_plus_ad_id": "a1",
                        "ad_name": "Summer Sale Ad",
                    }
                ],
                "page_info": {"total_page": 1},
            }
        }

    def get_ad(self, advertiser_id: str, ad_id: str, *, smart_plus: bool = False, **kwargs):
        return {
            "smart_plus_ad_id": ad_id,
            "ad_name": "Summer Sale Ad",
            "creative_list": [
                {
                    "creative_info": {
                        "identity_id": "identity1",
                        "identity_type": "CUSTOMIZED",
                        "identity_authorized_bc_id": "bc1",
                        "material_name": "Summer Sale Creative",
                    }
                }
            ],
            "ad_configuration": {
                "tracking_info": {
                    "tracking_app_id": "app1",
                }
            },
            "ad_text_list": [{"ad_text": "Buy now summer sale"}],
            "landing_page_url_list": [{"landing_page_url": "https://example.com"}],
        }


class CreativeAssetCliTests(unittest.TestCase):
    def test_parser_exposes_unified_creative_assets_group(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "tiktok",
                "creative-assets",
                "portfolio",
                "select",
                "--advertiser-id",
                "123",
            ]
        )
        self.assertIs(args.func, commands.command_tiktok_creative_assets_portfolio_select)

        alias_args = parser.parse_args(
            [
                "tiktok",
                "creative-asset",
                "portfolio",
                "list",
                "--advertiser-id",
                "123",
            ]
        )
        self.assertIs(alias_args.func, commands.command_tiktok_creative_assets_portfolio_list)

        inspect_args = parser.parse_args(
            [
                "tiktok",
                "creative-assets",
                "portfolio",
                "inspect",
                "--advertiser-id",
                "123",
            ]
        )
        self.assertIs(inspect_args.func, commands.command_tiktok_creative_assets_portfolio_inspect)

        export_args = parser.parse_args(
            [
                "tiktok",
                "creative-assets",
                "portfolio",
                "export",
                "--advertiser-id",
                "123",
                "--output-file",
                "out.json",
            ]
        )
        self.assertIs(export_args.func, commands.command_tiktok_creative_assets_portfolio_export)

        match_args = parser.parse_args(
            [
                "tiktok",
                "creative-assets",
                "portfolio",
                "match-campaign",
                "--advertiser-id",
                "123",
                "--campaign-id",
                "c1",
            ]
        )
        self.assertIs(match_args.func, commands.command_tiktok_creative_assets_portfolio_match_campaign)

    def test_select_tiktok_creative_portfolios_ranks_matches(self) -> None:
        portfolios = [
            {
                "creative_portfolio_id": "p1",
                "creative_portfolio_type": "CTA",
                "creative_portfolio_content": {
                    "title": "Summer Sale",
                    "primary_text": "Buy now",
                    "secondary_text": "Today only",
                },
            },
            {
                "creative_portfolio_id": "p2",
                "creative_portfolio_type": "CTA",
                "creative_portfolio_content": {
                    "title": "Winter Sale",
                    "primary_text": "Buy later",
                },
            },
            {
                "creative_portfolio_id": "p3",
                "creative_portfolio_type": "CARD",
                "creative_portfolio_content": {
                    "title": "Unrelated",
                    "primary_text": "Other content",
                },
            },
        ]

        result = commands.select_tiktok_creative_portfolios(
            portfolios,
            creative_portfolio_types=["CTA"],
            title="Summer Sale",
            query="buy now",
            limit=5,
        )

        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["selected_count"], 2)
        self.assertEqual(result["best_match"]["creative_portfolio_id"], "p1")
        self.assertEqual(result["selected"][0]["creative_portfolio_id"], "p1")

    def test_build_creative_portfolio_create_payload_uses_convenience_flags(self) -> None:
        args = argparse.Namespace(
            advertiser_id="123",
            payload_json=None,
            payload_file=None,
            creative_portfolio_type="CTA",
            portfolio_content_json='[{"title":"Summer Sale","primary_text":"Buy now"}]',
            portfolio_content_file=None,
            title=None,
            ad_text=None,
            primary_text=None,
            secondary_text=None,
            description=None,
            content_url=None,
            card_type=None,
            app_id=None,
            origin_app_id=None,
            image_id=None,
            thumbnail_id=None,
            video_id=None,
            identity_id=None,
            identity_type=None,
            identity_authorized_bc_id=None,
            call_to_action=None,
            product_source=None,
            product_set_id=None,
            catalog_id=None,
            catalog_authorized_bc_id=None,
            store_id=None,
            store_authorized_bc_id=None,
            asset_ids=None,
            sku_ids=None,
            item_group_ids=None,
            tags=None,
            selling_points=None,
            advanced_audio_video_id=None,
            advanced_gesture_icon_image_id=None,
            advanced_gesture_image_image_id=None,
        )

        payload = commands.build_creative_portfolio_create_payload(args)

        self.assertEqual(payload["advertiser_id"], "123")
        self.assertEqual(payload["creative_portfolio_type"], "CTA")
        self.assertEqual(payload["portfolio_content"][0]["title"], "Summer Sale")

    def test_build_creative_portfolio_create_payload_from_flags(self) -> None:
        args = argparse.Namespace(
            advertiser_id="123",
            payload_json=None,
            payload_file=None,
            creative_portfolio_type="CTA",
            portfolio_content_json=None,
            portfolio_content_file=None,
            title="Summer Sale",
            ad_text="Summer Sale",
            primary_text="Buy now",
            secondary_text="Today only",
            description="Best offer",
            content_url="https://example.com",
            card_type="IMAGE",
            app_id="app1",
            origin_app_id="app0",
            image_id="image1",
            thumbnail_id="thumb1",
            video_id="video1",
            identity_id="identity1",
            identity_type="CUSTOMIZED",
            identity_authorized_bc_id="bc1",
            call_to_action="SHOP_NOW",
            product_source="CATALOG",
            product_set_id="ps1",
            catalog_id="cat1",
            catalog_authorized_bc_id="bc2",
            store_id="store1",
            store_authorized_bc_id="bc3",
            asset_ids=["asset1", "asset2"],
            sku_ids=["sku1"],
            item_group_ids=["ig1"],
            tags=["tag1"],
            card_tags=["7", "8"],
            selling_points=["free trial"],
            country_codes=["US", "CA"],
            layouts=["LAYOUT_A"],
            advanced_audio_video_id="video-adv",
            advanced_gesture_icon_image_id="icon1",
            advanced_gesture_image_image_id="gesture1",
            advanced_show_time=12,
            advanced_interact_type=3,
            advanced_interact_shape=4,
            context_info_json=None,
            context_info_file=None,
            context_app_id="app-context",
            context_core_user_id="101",
            context_developer_id="202",
            context_x_forwarded_for="1.2.3.4",
            context_x_real_ip="5.6.7.8",
            context_user_agent="agent",
            context_referer="ref",
        )

        payload = commands.build_creative_portfolio_create_payload(args)

        content = payload["portfolio_content"][0]
        self.assertEqual(content["title"], "Summer Sale")
        self.assertEqual(content["primary_text"], "Buy now")
        self.assertEqual(content["identity_id"], "identity1")
        self.assertEqual(content["asset_ids"], ["asset1", "asset2"])
        self.assertEqual(content["advanced_audio_info"]["video_id"], "video-adv")
        self.assertEqual(content["card_tags"], ["7", "8"])
        self.assertEqual(content["country_code"], ["US", "CA"])
        self.assertEqual(content["layouts"], ["LAYOUT_A"])
        self.assertEqual(content["advanced_show_time"], 12)
        self.assertEqual(payload["context_info"]["app_id"], "app-context")

    def test_portfolio_select_command_uses_paged_client(self) -> None:
        client = FakeCreativeClient(
            [
                [
                    {
                        "creative_portfolio_id": "p1",
                        "creative_portfolio_type": "CTA",
                        "creative_portfolio_content": {"title": "Summer Sale", "primary_text": "Buy now"},
                    }
                ],
                [
                    {
                        "creative_portfolio_id": "p2",
                        "creative_portfolio_type": "CTA",
                        "creative_portfolio_content": {"title": "Winter Sale", "primary_text": "Later"},
                    }
                ],
            ]
        )
        args = argparse.Namespace(
            advertiser_id="123",
            access_token="token",
            key=None,
            refresh_token=False,
            json=True,
            payload_json=None,
            payload_file=None,
            creative_portfolio_ids=None,
            creative_portfolio_types=["CTA"],
            title="Summer",
            query=None,
            limit=10,
            page_size=1,
            max_pages=10,
        )

        with patch("motata_cli.tiktok.commands.resolve_tiktok_client", return_value=("123", client)), patch(
            "motata_cli.tiktok.commands.print_output"
        ) as mocked_print:
            commands.command_tiktok_creative_assets_portfolio_select(args)

        self.assertEqual(len(client.calls), 2)
        self.assertTrue(mocked_print.called)
        printed_payload = mocked_print.call_args.args[0]
        self.assertEqual(printed_payload["best_match"]["creative_portfolio_id"], "p1")
        self.assertEqual(printed_payload["advertiser_id"], "123")

    def test_portfolio_inspect_writes_export_when_requested(self) -> None:
        client = FakeCreativeClient(
            [
                [
                    {
                        "creative_portfolio_id": "p1",
                        "creative_portfolio_type": "CTA",
                        "creative_portfolio_content": {"title": "Summer Sale", "primary_text": "Buy now"},
                    }
                ]
            ]
        )
        args = argparse.Namespace(
            advertiser_id="123",
            access_token="token",
            key=None,
            refresh_token=False,
            json=True,
            payload_json=None,
            payload_file=None,
            creative_portfolio_ids=["p1"],
            creative_portfolio_types=None,
            title=None,
            query=None,
            limit=10,
            page_size=20,
            max_pages=10,
            include_raw=True,
            output_file=None,
        )

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "motata_cli.tiktok.commands.resolve_tiktok_client", return_value=("123", client)
        ), patch("motata_cli.tiktok.commands.print_output") as mocked_print:
            args.output_file = str(Path(tmpdir) / "inspect.json")
            commands.command_tiktok_creative_assets_portfolio_inspect(args)

            self.assertTrue(Path(args.output_file).exists())
            self.assertTrue(mocked_print.called)
            self.assertEqual(mocked_print.call_args.args[0]["inspected"][0]["summary"]["creative_portfolio_id"], "p1")

    def test_portfolio_export_requires_output_file(self) -> None:
        args = argparse.Namespace(
            advertiser_id="123",
            access_token="token",
            key=None,
            refresh_token=False,
            json=True,
            payload_json=None,
            payload_file=None,
            creative_portfolio_ids=["p1"],
            creative_portfolio_types=None,
            title=None,
            query=None,
            limit=10,
            page_size=20,
            max_pages=10,
            output_file="",
        )
        client = FakeCreativeClient([[]])

        with patch("motata_cli.tiktok.commands.resolve_tiktok_client", return_value=("123", client)):
            with self.assertRaises(commands.CliError):
                commands.command_tiktok_creative_assets_portfolio_export(args)

    def test_match_campaign_scores_realistic_portfolio(self) -> None:
        client = FakeCreativeClient(
            [
                [
                    {
                        "creative_portfolio_id": "p-match",
                        "creative_portfolio_type": "CTA",
                        "creative_portfolio_content": {
                            "title": "Summer Sale Creative",
                            "primary_text": "Buy now summer sale",
                            "identity_id": "identity1",
                            "app_id": "app1",
                        },
                    },
                    {
                        "creative_portfolio_id": "p-other",
                        "creative_portfolio_type": "CTA",
                        "creative_portfolio_content": {
                            "title": "Winter Sale",
                            "primary_text": "Other text",
                        },
                    },
                ]
            ]
        )
        result = commands.match_tiktok_creative_portfolios_from_campaign(
            client,
            advertiser_id="123",
            campaign_id="c1",
            smart_plus=True,
            limit=5,
            page_size=20,
            max_pages=10,
        )

        self.assertEqual(result["best_match"]["creative_portfolio_id"], "p-match")
        self.assertGreater(result["selected"][0]["score"], result["selected"][1]["score"])

    def test_build_tiktok_aigc_create_payload_uses_payload_video_type_for_product_bootstrap(self) -> None:
        args = argparse.Namespace(
            advertiser_id="123",
            product_url="https://shop.example.com/products/demo",
            payload_json='{"aigc_video_type":"TRYON"}',
            payload_file=None,
            input_video_ids=None,
            input_image_urls=None,
            product_name=None,
            title=None,
            description=None,
            brand=None,
            price=None,
            currency=None,
            selling_points=None,
            source_language=None,
            target_language=None,
            video_generation_count=None,
            voice_id=None,
            video_duration=None,
            subtitle_enabled=None,
            avatar_id=None,
        )
        context = {
            "product": {
                "url": args.product_url,
                "name": "Demo Product",
                "price": "$12.00",
            },
            "image_urls": [
                "https://cdn.example.com/demo-1.jpg",
                "https://cdn.example.com/demo-2.jpg",
                "https://cdn.example.com/demo-3.jpg",
            ],
            "image_ids": [],
            "uploaded_assets": [],
        }

        with patch("motata_cli.tiktok.commands.fetch_product_context", return_value=context):
            payload = commands.build_tiktok_aigc_create_payload(
                args,
                label="AIGC video payload",
                client=object(),
                mode="video",
                video_type=None,
            )

        self.assertEqual(payload["aigc_video_type"], "TRYON")
        self.assertNotIn("material_packages", payload)
        self.assertEqual(
            payload["product_video_info"]["input_image_list"]["image_url_list"],
            context["image_urls"],
        )
        self.assertEqual(payload["product_video_info"]["product_info_list"][0]["product_name"], "Demo Product")


if __name__ == "__main__":
    unittest.main()
