from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from motata_cli.meta.commands import (
    adset_uses_app_promoted_object,
    build_validation_adset_args,
    build_validation_campaign_args,
    build_story_spec,
    CliError,
    command_migrate_run,
    create_creative,
    detect_creative_migration_mode,
    load_required_migration_export,
    remap_promoted_object_for_target,
    validate_migration_preflight,
)


class MetaMigrateExportTests(unittest.TestCase):
    def test_load_required_migration_export_requires_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir)

            with self.assertRaises(CliError) as ctx:
                load_required_migration_export(export_dir)

            self.assertIn("Migration export is incomplete", str(ctx.exception))
            self.assertIn("asset-tree.json", str(ctx.exception))
            self.assertIn("creatives.raw.json", str(ctx.exception))

    def test_load_required_migration_export_validates_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir)
            (export_dir / "asset-tree.json").write_text(json.dumps({"tree": {}}), encoding="utf-8")
            (export_dir / "creatives.raw.json").write_text(json.dumps([]), encoding="utf-8")

            with self.assertRaises(CliError) as ctx:
                load_required_migration_export(export_dir)

            self.assertIn("top-level 'tree' list", str(ctx.exception))

    def test_load_required_migration_export_requires_creatives_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir)
            (export_dir / "asset-tree.json").write_text(json.dumps({"tree": []}), encoding="utf-8")
            (export_dir / "creatives.raw.json").write_text(json.dumps([]), encoding="utf-8")

            with self.assertRaises(CliError) as ctx:
                load_required_migration_export(export_dir)

            self.assertIn("must contain a JSON object keyed by creative ID", str(ctx.exception))


class MetaMigratePreflightTests(unittest.TestCase):
    def test_validate_migration_preflight_requires_complete_target_app_mapping(self) -> None:
        asset_tree = {"tree": []}

        with self.assertRaises(CliError) as ctx:
            validate_migration_preflight(asset_tree, target_application_id="123")

        self.assertIn("Target app mapping is incomplete", str(ctx.exception))
        self.assertIn("--target-object-store-url", str(ctx.exception))

    def test_validate_migration_preflight_blocks_sales_without_promoted_object(self) -> None:
        asset_tree = {
            "tree": [
                {
                    "campaign": {"id": "cmp-1", "objective": "OUTCOME_SALES"},
                    "adsets": [
                        {
                            "adset": {
                                "id": "adset-1",
                                "optimization_goal": "LANDING_PAGE_VIEWS",
                            },
                            "ads": [],
                        }
                    ],
                }
            ]
        }

        with self.assertRaises(CliError) as ctx:
            validate_migration_preflight(asset_tree)

        self.assertIn("OUTCOME_SALES", str(ctx.exception))
        self.assertIn("has no promoted_object", str(ctx.exception))

    def test_validate_migration_preflight_blocks_app_installs_without_app_fields(self) -> None:
        asset_tree = {
            "tree": [
                {
                    "campaign": {"id": "cmp-2", "objective": "OUTCOME_APP_PROMOTION"},
                    "adsets": [
                        {
                            "adset": {
                                "id": "adset-2",
                                "optimization_goal": "APP_INSTALLS",
                                "promoted_object": {},
                            },
                            "ads": [],
                        }
                    ],
                }
            ]
        }

        with self.assertRaises(CliError) as ctx:
            validate_migration_preflight(asset_tree)

        self.assertIn("APP_INSTALLS", str(ctx.exception))
        self.assertIn("application_id", str(ctx.exception))
        self.assertIn("object_store_url", str(ctx.exception))


class MetaMigratePromotedObjectMappingTests(unittest.TestCase):
    def test_remap_promoted_object_for_target_rewrites_known_fields(self) -> None:
        mapped = remap_promoted_object_for_target(
            {
                "page_id": "old-page",
                "pixel_id": "old-pixel",
                "application_id": "old-app",
                "object_store_url": "https://old.example/app",
                "custom_event_type": "PURCHASE",
            },
            target_page_id="new-page",
            target_pixel_id="new-pixel",
            target_application_id="new-app",
            target_object_store_url="https://new.example/app",
        )

        self.assertEqual(mapped["page_id"], "new-page")
        self.assertEqual(mapped["pixel_id"], "new-pixel")
        self.assertEqual(mapped["application_id"], "new-app")
        self.assertEqual(mapped["object_store_url"], "https://new.example/app")
        self.assertEqual(mapped["custom_event_type"], "PURCHASE")

    def test_remap_promoted_object_for_target_applies_overrides_last(self) -> None:
        mapped = remap_promoted_object_for_target(
            {"pixel_id": "old-pixel"},
            target_pixel_id="new-pixel",
            overrides={"pixel_id": "override-pixel"},
        )

        self.assertEqual(mapped["pixel_id"], "override-pixel")


class MetaMigrateValidationPayloadTests(unittest.TestCase):
    def test_build_validation_campaign_args_preserves_campaign_controls(self) -> None:
        args = build_validation_campaign_args(
            {
                "objective": "OUTCOME_APP_PROMOTION",
                "daily_budget": "1100",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                "bid_cap": None,
                "special_ad_categories": [],
            },
            name="tmp-campaign",
        )

        self.assertEqual(args.name, "tmp-campaign")
        self.assertEqual(args.objective, "OUTCOME_APP_PROMOTION")
        self.assertEqual(args.daily_budget, "1100")
        self.assertEqual(args.bid_strategy, "LOWEST_COST_WITHOUT_CAP")
        self.assertEqual(args.special_ad_categories, [])

    def test_build_validation_adset_args_preserves_delivery_controls(self) -> None:
        args = build_validation_adset_args(
            {
                "optimization_goal": "APP_INSTALLS",
                "billing_event": "IMPRESSIONS",
                "targeting": {"geo_locations": {"countries": ["US"]}},
                "daily_budget": None,
                "lifetime_budget": None,
                "bid_amount": 100,
                "bid_strategy": "COST_CAP",
                "bid_constraints": {"roas_average_floor": 1.5},
                "start_time": "2026-05-06T00:00:00+00:00",
                "end_time": "2026-05-07T00:00:00+00:00",
                "destination_type": "APP",
            },
            campaign_id="123",
            promoted_object={"application_id": "app-1", "object_store_url": "https://example.com/app"},
            name="tmp-adset",
        )

        self.assertEqual(args.campaign_id, "123")
        self.assertEqual(args.name, "tmp-adset")
        self.assertEqual(args.bid_amount, 100)
        self.assertEqual(args.bid_strategy, "COST_CAP")
        self.assertIn('"roas_average_floor":1.5', args.bid_constraints_json)
        self.assertEqual(args.destination_type, "APP")
        self.assertEqual(args.start_time, "2026-05-06T00:00:00+00:00")
        self.assertEqual(args.end_time, "2026-05-07T00:00:00+00:00")

    def test_adset_uses_app_promoted_object_detects_app_context(self) -> None:
        self.assertTrue(
            adset_uses_app_promoted_object(
                {"objective": "OUTCOME_APP_PROMOTION"},
                {"optimization_goal": "APP_INSTALLS", "destination_type": "APP", "promoted_object": {}},
            )
        )
        self.assertFalse(
            adset_uses_app_promoted_object(
                {"objective": "OUTCOME_SALES"},
                {
                    "optimization_goal": "OFFSITE_CONVERSIONS",
                    "destination_type": "UNDEFINED",
                    "promoted_object": {"pixel_id": "123", "custom_event_type": "PURCHASE"},
                },
            )
        )


class MetaMigrateCreativeModeTests(unittest.TestCase):
    def test_create_creative_does_not_infer_instagram_actor_id(self) -> None:
        class FakeMeta:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object] | None]] = []

            def post(self, path: str, data: dict[str, object] | None = None, files: object | None = None) -> dict[str, str]:
                self.calls.append((path, data))
                return {"id": "creative-1"}

        meta = FakeMeta()
        create_creative(
            meta,
            "766290062019765",
            argparse.Namespace(
                name="creative",
                page_id="105968052579027",
                instagram_user_id="17841460579987262",
                instagram_actor_id=None,
                video_id=None,
                image_hash="abc123",
                message="let play it",
                headline="Jumping Chicken!",
                description=None,
                link="http://itunes.apple.com/app/id1522004076",
                call_to_action="INSTALL_MOBILE_APP",
                object_story_id=None,
                object_story_spec=None,
                asset_feed_spec=None,
                url_tags=None,
            ),
        )

        self.assertEqual(len(meta.calls), 1)
        _, payload = meta.calls[0]
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertNotIn("instagram_actor_id", payload)
        self.assertIn("instagram_user_id", payload["object_story_spec"])

    def test_build_story_spec_link_carries_call_to_action_link_value(self) -> None:
        story_spec = build_story_spec(
            type(
                "Args",
                (),
                {
                    "object_story_spec": None,
                    "page_id": "105968052579027",
                    "instagram_user_id": "17841460579987262",
                    "video_id": None,
                    "image_hash": "abc123",
                    "link": "http://itunes.apple.com/app/id1522004076",
                    "message": "let play it",
                    "headline": "Jumping Chicken!",
                    "description": None,
                    "call_to_action": "INSTALL_MOBILE_APP",
                },
            )()
        )

        self.assertEqual(
            story_spec["link_data"]["call_to_action"],
            {
                "type": "INSTALL_MOBILE_APP",
                "value": {"link": "http://itunes.apple.com/app/id1522004076"},
            },
        )

    def test_detect_creative_migration_mode_video(self) -> None:
        creative = {
            "id": "video-1",
            "object_story_spec": {
                "video_data": {
                    "video_id": "123",
                }
            },
        }

        self.assertEqual(detect_creative_migration_mode(creative), "video")

    def test_detect_creative_migration_mode_existing_post(self) -> None:
        creative = {
            "id": "post-1",
            "object_story_id": "123_456",
        }

        self.assertEqual(detect_creative_migration_mode(creative), "existing_post")

    def test_detect_creative_migration_mode_link(self) -> None:
        creative = {
            "id": "link-1",
            "object_story_spec": {
                "link_data": {
                    "link": "https://example.com",
                }
            },
        }

        self.assertEqual(detect_creative_migration_mode(creative), "link")


class MetaMigrateRuntimeBehaviorTests(unittest.TestCase):
    def test_command_migrate_run_does_not_auto_discover_target_instagram_user_id(self) -> None:
        class FakeMeta:
            def __init__(self) -> None:
                self.get_calls: list[tuple[str, dict[str, object] | None]] = []
                self.paginate_calls: list[tuple[str, dict[str, object] | None]] = []

            def get(self, path: str, params: dict[str, object] | None = None) -> dict[str, object]:
                self.get_calls.append((path, params))
                raise AssertionError("command_migrate_run should not auto-fetch page Instagram IDs")

            def paginate(self, path: str, params: dict[str, object] | None = None) -> list[dict[str, object]]:
                self.paginate_calls.append((path, params))
                return []

        source_meta = FakeMeta()
        target_meta = FakeMeta()
        printed: list[dict[str, object]] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            export_dir = Path(tmpdir)
            (export_dir / "asset-tree.json").write_text(json.dumps({"tree": []}), encoding="utf-8")
            (export_dir / "creatives.raw.json").write_text(json.dumps({}), encoding="utf-8")

            args = argparse.Namespace(
                export_dir=str(export_dir),
                source_account_id="1015303836971442",
                target_account_id="766290062019765",
                page_id="105968052579027",
                instagram_user_id=None,
                pixel_id="1502793701436178",
                target_application_id=None,
                target_object_store_url=None,
                promoted_object_overrides_json=None,
                reuse_existing_by_name=False,
                source_access_token="source-token",
                target_access_token="target-token",
                job_id="job-1",
            )

            with (
                patch("motata_cli.meta.commands.build_auth_from_args", side_effect=[argparse.Namespace(access_token="source-token"), argparse.Namespace(access_token="target-token")]),
                patch("motata_cli.meta.commands.MetaClient", side_effect=[source_meta, target_meta]),
                patch("motata_cli.meta.commands.validate_migration_preflight"),
                patch("motata_cli.meta.commands.validate_target_promoted_objects"),
                patch("motata_cli.meta.commands.validate_target_app_ad_links"),
                patch("motata_cli.meta.commands.write_json_file"),
                patch("motata_cli.meta.commands.JOBS_DIR", export_dir / "jobs"),
                patch(
                    "motata_cli.meta.commands.print_output",
                    side_effect=lambda payload, as_json=False: printed.append(payload),
                ),
            ):
                command_migrate_run(args)
                saved_job = json.loads((export_dir / "jobs/job-1.json").read_text())

        self.assertEqual(target_meta.get_calls, [])
        self.assertEqual(len(printed), 1)
        self.assertIsNone(printed[0]["target_instagram_user_id"])
        self.assertEqual(saved_job["status"], "completed")
        self.assertIsNone(saved_job["config"]["instagram_user_id"])


if __name__ == "__main__":
    unittest.main()
