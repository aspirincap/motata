"""Offline stage-four service boundary and ambiguous upload regressions."""
from __future__ import annotations

import argparse
import ast
import builtins
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from motata_cli.common.errors import CliError
from motata_cli.meta import commands
from motata_cli.meta.services import discovery, media, migrate, migration_assets, resources


class MediaWriteSafetyTests(unittest.TestCase):
    def test_ambiguous_writes_are_never_replayed(self):
        for phase in (None, "start", "transfer", "finish"):
            for error in (requests.Timeout("timeout"), requests.ConnectionError("disconnect"),
                          CliError('{"error":{"code":2}}')):
                with self.subTest(phase=phase, error=type(error).__name__):
                    client = Mock()
                    client.post.side_effect = error
                    data = {"upload_phase": phase}
                    if phase == "transfer":
                        # A requested offset is not a verified server offset.
                        data.update(upload_session_id="session", start_offset="0")
                    with patch.object(media.time, "sleep") as sleep:
                        with self.assertRaisesRegex(CliError, "not automatically retried"):
                            media.video_post_with_retry(client, "act_1/advideos", data=data, context="test")
                    client.post.assert_called_once()
                    sleep.assert_not_called()

    def test_chunked_upload_stops_at_failed_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.write_bytes(b"abc")
            responses = [
                {"upload_session_id": "s", "video_id": "v", "start_offset": "0", "end_offset": "3"},
                {"start_offset": "3", "end_offset": "3"},
            ]
            for failed_index in range(3):
                with self.subTest(failed_index=failed_index):
                    client = Mock()
                    client.post.side_effect = responses[:failed_index] + [requests.Timeout()]
                    with self.assertRaises(CliError):
                        media.chunked_upload_video(client, "1", path, name=None, title=None)
                    self.assertEqual(client.post.call_count, failed_index + 1)

    def test_single_upload_timeout_is_one_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "video.mp4"
            path.write_bytes(b"abc")
            client = Mock()
            client.post.side_effect = requests.Timeout()
            with self.assertRaises(CliError):
                media.single_upload_video(client, "1", path, name=None, title=None)
            client.post.assert_called_once()

    def test_compatibility_exports_point_to_services(self):
        self.assertIs(commands.upload_video, media.upload_video)
        self.assertIs(commands.video_post_with_retry, media.video_post_with_retry)
        self.assertIs(commands.infer_promotable_pages, discovery.infer_promotable_pages)
        self.assertIs(commands.load_required_migration_export, migration_assets.load_required_migration_export)


class MigrationBoundaryTests(unittest.TestCase):
    def test_export_and_plan_use_explicit_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deps = migrate.MigrationDependencies(
                get_entity=Mock(return_value={"id": "c"}), list_entities=Mock(return_value=[]),
                infer_promotable_pages=Mock(return_value={"pages": []}),
                discover_pixels=Mock(return_value=[]),
            )
            result = migrate.export_migration_bundle(Mock(), source_account_id="1", campaign_ids=["c"],
                                                      export_dir=root, commands_module=deps)
            self.assertEqual(result["counts"]["campaigns"], 1)
            result = migrate.build_migration_plan_summary(export_dir=root, source_account_id="1",
                target_account_id="2", target_meta=Mock(), commands_module=deps)
            self.assertEqual(result["target_page_candidates"], [])
            deps.infer_promotable_pages.assert_called_once()

    def test_default_export_and_legacy_wrapper_patch_seams(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (patch.object(resources, "get_entity", return_value={"id": "c"}) as get,
                  patch.object(resources, "list_entities", return_value=[])):
                migrate.export_migration_bundle(Mock(), source_account_id="1", campaign_ids=["c"],
                                                export_dir=Path(tmp))
                get.assert_called_once()
            with (patch.object(commands, "get_entity", return_value={"id": "c"}) as get,
                  patch.object(commands, "list_entities", return_value=[])):
                commands.export_migration_bundle(Mock(), source_account_id="1", campaign_ids=["c"],
                                                 export_dir=Path(tmp))
                get.assert_called_once()

    def test_default_run_uses_low_level_patches_without_commands_import(self):
        real_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name.endswith("commands") or "commands" in (fromlist or ()):
                raise AssertionError("Business service attempted a commands import")
            return real_import(name, globals, locals, fromlist, level)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(export_dir=str(root), source_account_id="1", target_account_id="2",
                page_id="3", pixel_id="4", instagram_user_id=None, target_application_id=None,
                target_object_store_url=None, promoted_object_overrides_json=None,
                reuse_existing_by_name=True, source_access_token="fake", target_access_token="fake", job_id="test")
            tree = {"tree": [{"campaign": {"id": "c", "name": "campaign", "objective": "OUTCOME_TRAFFIC"}, "adsets": []}]}
            with (patch.object(migrate, "JOBS_DIR", root / "jobs"),
                  patch.object(migrate, "build_auth_from_args", return_value=argparse.Namespace(access_token="fake")),
                  patch.object(migrate, "MetaClient"),
                  patch.object(migration_assets, "load_required_migration_export", return_value=(tree, {})),
                  patch.object(resources, "create_campaign", return_value={"id": "new"}) as create,
                  patch("builtins.__import__", side_effect=guarded_import)):
                result = migrate.run_migration_flow(args)
                self.assertEqual(result["old_to_new_campaigns"], {"c": "new"})
                create.assert_called_once()
                self.assertEqual(create.call_args.args[2].status, "PAUSED")

    def test_business_modules_have_no_commands_reverse_import(self):
        root = Path(migrate.__file__).parents[1]
        files = list((root / "services").glob("*.py")) + [root / "utils.py", root / "payloads.py", root / "preflight.py"]
        for path in files:
            with self.subTest(path=path.name):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Import):
                        self.assertFalse(any(alias.name.endswith(".commands") for alias in node.names))
                    elif isinstance(node, ast.ImportFrom):
                        self.assertFalse((node.module or "").endswith(".commands"))
                        self.assertFalse(any(alias.name == "commands" for alias in node.names))


if __name__ == "__main__":
    unittest.main()
