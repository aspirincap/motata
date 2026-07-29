from __future__ import annotations

import unittest

from motata_cli.tiktok.commands import (
    _apply_identity_video_detail,
    enrich_gmv_max_item_identity_video_info,
)


class GmvMaxItemPreviewTests(unittest.TestCase):
    def test_identity_video_detail_refreshes_cover_and_preserves_campaign_author(self) -> None:
        row = {
            "item_id": "item-1",
            "identity_info": {
                "identity_type": "BC_AUTH_TT",
                "identity_id": "identity-1",
                "identity_authorized_bc_id": "bc-1",
                "user_name": "campaign_user",
                "profile_image": "https://example.com/old-avatar.jpg",
            },
            "video_info": {
                "video_cover_url": "https://example.com/old-cover.jpg",
                "preview_url": "https://example.com/old-preview.mp4",
            },
        }

        _apply_identity_video_detail(
            row,
            {
                "item_id": "item-1",
                "text": "fresh text",
                "user_info": {
                    "user_name": "api_user",
                    "profile_image": "https://example.com/new-avatar.jpg",
                },
                "video_info": {
                    "poster_url": "https://example.com/new-cover.jpg",
                    "url": "https://example.com/new-preview.mp4",
                },
            },
        )

        self.assertEqual(row["text"], "fresh text")
        self.assertEqual(row["identity_info"]["user_name"], "campaign_user")
        self.assertEqual(row["identity_info"]["profile_image"], "https://example.com/new-avatar.jpg")
        self.assertEqual(row["video_info"]["video_cover_url"], "https://example.com/new-cover.jpg")
        self.assertEqual(row["video_info"]["preview_url"], "https://example.com/new-preview.mp4")

    def test_identity_video_enrichment_batches_target_items_by_identity(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def get_identity_video_info(self, advertiser_id: str, **kwargs):
                self.calls.append({"advertiser_id": advertiser_id, **kwargs})
                return {
                    "data": {
                        "video_details": [
                            {
                                "item_id": item_id,
                                "video_info": {
                                    "poster_url": f"https://example.com/{item_id}.jpg",
                                    "url": f"https://example.com/{item_id}.mp4",
                                },
                            }
                            for item_id in kwargs["item_ids"]
                        ]
                    }
                }

        rows = {
            "item-1": {
                "item_id": "item-1",
                "identity_info": {
                    "identity_type": "BC_AUTH_TT",
                    "identity_id": "identity-1",
                    "identity_authorized_bc_id": "bc-1",
                },
            },
            "item-2": {
                "item_id": "item-2",
                "identity_info": {
                    "identity_type": "BC_AUTH_TT",
                    "identity_id": "identity-1",
                    "identity_authorized_bc_id": "bc-1",
                },
            },
            "item-3": {
                "item_id": "item-3",
                "identity_info": {
                    "identity_type": "TT_USER",
                    "identity_id": "identity-2",
                },
            },
        }

        client = FakeClient()
        summary = enrich_gmv_max_item_identity_video_info(
            client,
            advertiser_id="adv-1",
            rows_by_item_id=rows,
            target_item_ids=["item-1", "item-2"],
        )

        self.assertEqual(summary["call_count"], 1)
        self.assertEqual(summary["enriched_item_count"], 2)
        self.assertEqual(client.calls[0]["identity_type"], "BC_AUTH_TT")
        self.assertEqual(client.calls[0]["item_ids"], ["item-1", "item-2"])
        self.assertEqual(client.calls[0]["identity_authorized_bc_id"], "bc-1")
        self.assertEqual(rows["item-1"]["video_info"]["video_cover_url"], "https://example.com/item-1.jpg")
        self.assertNotIn("video_info", rows["item-3"])


if __name__ == "__main__":
    unittest.main()
