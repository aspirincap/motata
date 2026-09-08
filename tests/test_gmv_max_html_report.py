from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


from motata_cli.report import gmv_max_html as gmv_report


class GmvMaxCreativeJudgementTests(unittest.TestCase):
    def test_aggregate_creatives_preserves_official_creative_fields(self) -> None:
        rows = [
            {
                "dimensions": {"item_id": "item-1", "item_scope": "SPECIFIC_ITEM"},
                "metrics": {
                    "creative_delivery_status": "DELIVERING",
                    "cost": "20",
                    "gross_revenue": "100",
                    "orders": "4",
                    "product_impressions": "1000",
                    "product_clicks": "50",
                },
            }
        ]

        item = gmv_report.aggregate_creatives(rows)["item-1"]

        self.assertEqual(item["roi"], 5.0)
        self.assertEqual(item["cost_per_order"], 5.0)
        self.assertEqual(item["product_gpm"], 100.0)
        self.assertEqual(item["delivery_statuses"], ["DELIVERING"])

    def test_new_item_with_normal_basics_is_learning_observe(self) -> None:
        judgement = gmv_report.creative_judgement(
            {
                "cost": 80,
                "roi": 0.8,
                "product_gpm": 2.0,
                "cpm_proxy": 2.5,
                "product_click_rate": 9.0,
                "ad_video_view_rate_2s": 55.0,
            },
            None,
            {
                "roi": 2.0,
                "product_click_rate": 8.0,
                "ad_video_view_rate_2s": 50.0,
                "product_gpm": 2.0,
                "cpm_proxy": 2.0,
                "high_spend": 50,
                "very_high_spend": 100,
            },
            {},
        )

        self.assertEqual(judgement["label"], "学习期观察")
        self.assertIn("不直接删除", judgement["action"])

    def test_former_winner_with_roi_drop_is_decay_replacement(self) -> None:
        judgement = gmv_report.creative_judgement(
            {
                "cost": 180,
                "roi": 0.7,
                "product_gpm": 0.8,
                "cpm_proxy": 2.0,
                "product_click_rate": 3.0,
                "ad_video_view_rate_2s": 35.0,
            },
            {"cost": 120, "roi": 3.0},
            {
                "roi": 2.0,
                "product_click_rate": 8.0,
                "ad_video_view_rate_2s": 50.0,
                "product_gpm": 2.0,
                "cpm_proxy": 2.0,
                "high_spend": 50,
                "very_high_spend": 100,
            },
            {"roi": 2.0},
        )

        self.assertEqual(judgement["label"], "衰退替换")
        self.assertIn("5-10", judgement["action"])

    def test_render_defaults_to_top_40_creatives_with_previews(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "manifest.json").write_text(
                '{"advertiser_id":"adv-1","tiktok_report_mode":"gmv_max","window":{"since":"2026-06-03","until":"2026-06-09","previous_since":"2026-05-27","previous_until":"2026-06-02"},"gmv_max":{"coverage":"full","store_ids":["store-1"]},"options":{"activity_strategy":"not_applicable_gmv_max"}}',
                encoding="utf-8",
            )
            for name in [
                "current_gmv_max_account",
                "previous_gmv_max_account",
                "current_gmv_max_campaign",
                "previous_gmv_max_campaign",
                "current_gmv_max_product",
                "previous_gmv_max_creative",
                "gmv_max_stores",
                "gmv_max_campaigns_product",
                "gmv_max_custom_anchor_videos",
                "gmv_max_videos",
            ]:
                (run_dir / f"{name}.json").write_text('{"rows":[]}', encoding="utf-8")

            rows = []
            for rank in range(1, 42):
                rows.append(
                    {
                        "dimensions": {"item_id": f"item-{rank}", "item_scope": "SPECIFIC_ITEM"},
                        "metrics": {
                            "creative_delivery_status": "DELIVERING",
                            "cost": str(100 - rank),
                            "gross_revenue": "1",
                            "orders": "1",
                            "product_impressions": "1000",
                            "product_clicks": "50",
                        },
                    }
                )
            rows.append(
                {
                    "dimensions": {
                        "campaign_id": "cmp-1",
                        "item_group_id": "spu-1",
                        "item_id": "item-product",
                        "item_scope": "SPECIFIC_ITEM",
                    },
                    "metrics": {
                        "creative_delivery_status": "DELIVERING",
                        "cost": "12.50",
                        "gross_revenue": "88.80",
                        "orders": "3",
                        "product_impressions": "2500",
                        "product_clicks": "125",
                    },
                }
            )
            (run_dir / "current_gmv_max_creative.json").write_text(
                gmv_report.json.dumps({"rows": rows}, ensure_ascii=False),
                encoding="utf-8",
            )
            (run_dir / "gmv_max_campaign_item_previews.json").write_text(
                gmv_report.json.dumps(
                    {
                        "campaign_ids": ["cmp-1"],
                        "by_item_id": {
                            "item-18": {
                                "item_id": "item-18",
                                "text": "rank eighteen preview",
                                "video_info": {"video_cover_url": "", "preview_url": ""},
                            },
                            "item-40": {
                                "item_id": "item-40",
                                "text": "rank forty preview",
                                "video_info": {"video_cover_url": "", "preview_url": ""},
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "gmv_max_resolved_item_previews.json").write_text(
                gmv_report.json.dumps(
                    {
                        "by_item_id": {
                            "item-40": {
                                "item_id": "item-40",
                                "post_url": "https://www.tiktok.com/@real/video/item-40",
                                "text": "resolved rank forty",
                                "identity_info": {
                                    "user_name": "real",
                                    "display_name": "Real Creator",
                                    "profile_image": "https://example.com/avatar.jpg",
                                },
                                "video_info": {"video_cover_url": "", "preview_url": "https://www.tiktok.com/@real/video/item-40"},
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "gmv_max_store_products.json").write_text(
                gmv_report.json.dumps(
                    {
                        "by_item_group_id": {
                            "spu-1": {
                                "item_group_id": "spu-1",
                                "title": "Compact Collagen Gummies With A Very Long Product Name",
                                "product_image_url": "assets/products/spu-1.jpg",
                                "min_price": "19.99",
                                "max_price": "29.99",
                                "currency": "USD",
                                "status": "AVAILABLE",
                                "gmv_max_ads_status": "OCCUPIED",
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "current_gmv_max_product.json").write_text(
                gmv_report.json.dumps(
                    {
                        "rows": [
                            {
                                "dimensions": {"item_group_id": "spu-1", "campaign_id_filter": "cmp-1"},
                                "metrics": {"product_status": "available", "orders": "3", "gross_revenue": "88.8"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            # Rendering assertions use fixture URLs; unit tests must not fetch
            # avatar images from the public internet.
            with patch.object(gmv_report, "cache_remote_images", return_value={}):
                html = gmv_report.render(run_dir)

            self.assertIn("Top 40 / 42", html)
            self.assertIn("产品 / Item Group", html)
            self.assertIn("Compact Collagen Gummies", html)
            self.assertIn("折后 USD 19.99", html)
            self.assertIn("定价 USD 29.99", html)
            self.assertIn("assets/products/spu-1.jpg", html)
            self.assertIn("Product GPM", html)
            self.assertIn("商品展示", html)
            self.assertIn("商品点击", html)
            self.assertIn("status-ok", html)
            self.assertNotIn("OCCUPIED", html)
            self.assertIn("item-40", html)
            self.assertIn("Real Creator", html)
            self.assertNotIn("@real", html)
            self.assertIn("https://www.tiktok.com/@motata/video/item-40", html)
            self.assertIn("预览帖子", html)
            self.assertIn("rank forty preview", html)
            self.assertIn("素材 / 账号 / 帖子", html)
            self.assertNotIn("<th>素材文案</th>", html)
            self.assertNotIn("item-41", html)

    def test_report_post_link_uses_motata_fallback_even_with_real_resolver_url(self) -> None:
        preview = gmv_report.resolver_result_to_preview(
            {
                "item_id": "7642299285320551694",
                "user_name": "@mgsueno2",
                "avatar_url": "https://example.com/avatar.jpg",
                "preview_url": "https://example.com/cover.jpg",
                "real_video_url": "https://www.tiktok.com/@mgsueno2/video/7642299285320551694",
            }
        )

        cell = gmv_report.creative_identity_cell("7642299285320551694", preview)

        self.assertIn("mgsueno2", cell)
        self.assertNotIn("@mgsueno2", cell)
        self.assertIn("https://www.tiktok.com/@motata/video/7642299285320551694", cell)
        self.assertNotIn("https://www.tiktok.com/@mgsueno2/video/7642299285320551694", cell)

    def test_combined_creative_cell_clips_copy_and_status_hover_lists_campaign(self) -> None:
        long_text = "这是一条很长的素材文案 " * 20
        item = {
            "item_id": "item-1",
            "titles": [],
            "delivery_statuses": ["REJECTED"],
            "status_campaigns": {"REJECTED": ["cmp-1"]},
            "rejection_reasons": [],
        }
        preview = {
            "item_id": "item-1",
            "text": long_text,
            "identity_info": {"display_name": "Creator One", "user_name": "creator_one"},
            "post_url": "https://www.tiktok.com/@motata/video/item-1",
        }

        copy_cell = gmv_report.creative_post_copy_cell("item-1", item, preview)
        status_cell = gmv_report.status_cell(item, {"cmp-1": "Campaign One"})

        self.assertIn("Creator One", copy_cell)
        self.assertIn("...", copy_cell)
        self.assertIn(long_text.strip(), copy_cell)
        self.assertIn("copy-short", copy_cell)
        self.assertIn("copy-full", copy_cell)
        self.assertNotIn("copy-popover", copy_cell)
        self.assertIn("拒审", status_cell)
        self.assertIn("status-popover", status_cell)
        self.assertIn("Campaign One", status_cell)
        self.assertIn("接口未返回具体原因", status_cell)

    def test_rejected_unavailable_status_changes_judgement_before_roi_deletion(self) -> None:
        judgement = gmv_report.creative_judgement(
            {
                "cost": 180,
                "roi": 0.4,
                "gross_revenue": 72,
                "orders": 2,
                "product_impressions": 1000,
                "product_gpm": 72,
                "cpm_proxy": 180,
                "product_click_rate": 2,
                "ad_video_view_rate_2s": 20,
                "delivery_statuses": ["REJECTED"],
            },
            {"cost": 120, "roi": 3.0, "product_gpm": 250},
            {
                "roi": 2.0,
                "product_click_rate": 8.0,
                "ad_video_view_rate_2s": 50.0,
                "product_gpm": 150.0,
                "cpm_proxy": 100.0,
                "high_spend": 50,
                "very_high_spend": 100,
            },
            {"roi": 2.0},
        )

        self.assertEqual(judgement["label"], "不可投替换")
        self.assertIn("拒审", judgement["reason"])


if __name__ == "__main__":
    unittest.main()
