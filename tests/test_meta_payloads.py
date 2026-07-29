from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from motata_cli.__main__ import build_parser
from motata_cli.meta.payloads import (
    build_ad_payload,
    build_adset_payload,
    build_campaign_payload,
    build_creative_payload,
)
from motata_cli.meta.specs import creative_spec_from


class MetaPayloadParserTests(unittest.TestCase):
    def test_creatives_update_parser_accepts_media_and_payload_inputs(self) -> None:
        parser = build_parser()

        args = parser.parse_args(
            [
                "meta",
                "creatives",
                "update",
                "123",
                "--access-token",
                "demo-token",
                "--media-sourcing-spec",
                '{"image_source":"LIBRARY"}',
                "--payload-json",
                '{"name":"demo"}',
            ]
        )

        self.assertEqual(args.creative_id, "123")
        self.assertEqual(args.media_sourcing_spec, '{"image_source":"LIBRARY"}')
        self.assertEqual(args.payload_json, '{"name":"demo"}')


class MetaPayloadBuilderTests(unittest.TestCase):
    def test_creative_spec_from_namespace_preserves_raw_fields(self) -> None:
        spec = creative_spec_from(
            argparse.Namespace(
                name="creative",
                media_sourcing_spec='{"image_source":"LIBRARY"}',
                payload_json='{"name":"base"}',
            )
        )

        self.assertEqual(spec.name, "creative")
        self.assertEqual(spec.media_sourcing_spec, '{"image_source":"LIBRARY"}')
        self.assertEqual(spec.payload_json, '{"name":"base"}')

    def test_build_creative_payload_compacts_media_sourcing_spec(self) -> None:
        payload = build_creative_payload(
            argparse.Namespace(
                name="creative",
                page_id=None,
                instagram_user_id=None,
                instagram_actor_id=None,
                video_id=None,
                image_hash=None,
                link=None,
                message=None,
                headline=None,
                description=None,
                call_to_action=None,
                object_story_id="123_456",
                object_story_spec=None,
                asset_feed_spec='{"bodies":[{"text":"hello"}]}',
                media_sourcing_spec='{"image_source":"LIBRARY"}',
                url_tags="utm_source=test",
                payload_json=None,
                payload_file=None,
            )
        )

        self.assertEqual(payload["name"], "creative")
        self.assertEqual(payload["object_story_id"], "123_456")
        self.assertEqual(payload["asset_feed_spec"], '{"bodies":[{"text":"hello"}]}')
        self.assertEqual(payload["media_sourcing_spec"], '{"image_source":"LIBRARY"}')
        self.assertEqual(payload["url_tags"], "utm_source=test")

    def test_build_creative_payload_merges_raw_payload_with_direct_story_inputs(self) -> None:
        payload = build_creative_payload(
            argparse.Namespace(
                name="override-name",
                page_id="105968052579027",
                instagram_user_id="17841460579987262",
                instagram_actor_id=None,
                video_id=None,
                image_hash="abc123",
                link="http://itunes.apple.com/app/id1522004076",
                message="let play it",
                headline="Jumping Chicken!",
                description=None,
                call_to_action="INSTALL_MOBILE_APP",
                object_story_id=None,
                object_story_spec=None,
                asset_feed_spec=None,
                media_sourcing_spec=None,
                url_tags=None,
                payload_json=json.dumps(
                    {
                        "name": "base-name",
                        "url_tags": "utm_source=base",
                        "object_story_spec": {
                            "page_id": "old-page",
                            "link_data": {"link": "https://old.example", "image_hash": "old"},
                        },
                    }
                ),
                payload_file=None,
            )
        )

        self.assertEqual(payload["name"], "override-name")
        self.assertEqual(payload["url_tags"], "utm_source=base")
        story_spec = json.loads(payload["object_story_spec"])
        self.assertEqual(story_spec["page_id"], "105968052579027")
        self.assertEqual(story_spec["instagram_user_id"], "17841460579987262")
        self.assertEqual(story_spec["link_data"]["image_hash"], "abc123")
        self.assertEqual(story_spec["link_data"]["call_to_action"]["type"], "INSTALL_MOBILE_APP")

    def test_build_creative_payload_keeps_raw_object_story_id_with_default_cta(self) -> None:
        payload = build_creative_payload(
            argparse.Namespace(
                name="validate-name",
                page_id=None,
                instagram_user_id=None,
                instagram_actor_id=None,
                video_id=None,
                image_hash=None,
                link=None,
                message=None,
                headline=None,
                description=None,
                call_to_action="SHOP_NOW",
                object_story_id=None,
                object_story_spec=None,
                asset_feed_spec=None,
                media_sourcing_spec=None,
                url_tags=None,
                payload_json=json.dumps(
                    {
                        "name": "raw-name",
                        "object_story_id": "123_456",
                        "url_tags": "utm_source=payload",
                    }
                ),
                payload_file=None,
            )
        )

        self.assertEqual(payload["name"], "validate-name")
        self.assertEqual(payload["object_story_id"], "123_456")
        self.assertNotIn("object_story_spec", payload)
        self.assertEqual(payload["url_tags"], "utm_source=payload")

    def test_build_campaign_payload_accepts_raw_payload_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            payload_file = Path(tmpdir) / "campaign.json"
            payload_file.write_text(
                json.dumps({"name": "raw-campaign", "objective": "OUTCOME_SALES", "special_ad_categories": []}),
                encoding="utf-8",
            )

            payload = build_campaign_payload(
                argparse.Namespace(
                    name=None,
                    objective=None,
                    status=None,
                    daily_budget=None,
                    lifetime_budget=None,
                    bid_strategy=None,
                    bid_cap=None,
                    bid_constraints_json=None,
                    special_ad_categories=[],
                    use_adset_level_budgets=False,
                    payload_json=None,
                    payload_file=str(payload_file),
                )
            )

        self.assertEqual(payload["name"], "raw-campaign")
        self.assertEqual(payload["objective"], "OUTCOME_SALES")
        self.assertEqual(payload["special_ad_categories"], "[]")

    def test_build_adset_payload_accepts_raw_promoted_object_payload(self) -> None:
        payload = build_adset_payload(
            argparse.Namespace(
                campaign_id=None,
                name=None,
                optimization_goal=None,
                billing_event=None,
                status=None,
                targeting_json=None,
                promoted_object_json=None,
                daily_budget=None,
                lifetime_budget=None,
                bid_amount=None,
                bid_strategy=None,
                bid_constraints_json=None,
                start_time=None,
                end_time=None,
                destination_type=None,
                frequency_cap_json=None,
                frequency_control_specs_json=None,
                payload_json=json.dumps(
                    {
                        "campaign_id": "123",
                        "name": "raw-adset",
                        "optimization_goal": "APP_INSTALLS",
                        "billing_event": "IMPRESSIONS",
                        "promoted_object": {
                            "application_id": "app-1",
                            "object_store_url": "https://example.com/app",
                        },
                    }
                ),
                payload_file=None,
            )
        )

        self.assertEqual(payload["campaign_id"], "123")
        self.assertEqual(payload["name"], "raw-adset")
        self.assertEqual(
            payload["promoted_object"],
            '{"application_id":"app-1","object_store_url":"https://example.com/app"}',
        )

    def test_build_ad_payload_accepts_raw_creative_object(self) -> None:
        payload = build_ad_payload(
            argparse.Namespace(
                name=None,
                adset_id=None,
                creative_id=None,
                status=None,
                bid_amount=None,
                tracking_specs_json=None,
                conversion_domain=None,
                payload_json=json.dumps(
                    {
                        "name": "raw-ad",
                        "adset_id": "adset-1",
                        "creative": {"creative_id": "creative-1"},
                    }
                ),
                payload_file=None,
            )
        )

        self.assertEqual(payload["name"], "raw-ad")
        self.assertEqual(payload["adset_id"], "adset-1")
        self.assertEqual(payload["creative"], '{"creative_id":"creative-1"}')


if __name__ == "__main__":
    unittest.main()
