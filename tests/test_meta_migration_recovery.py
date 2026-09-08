from __future__ import annotations

import argparse
import json
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from motata_cli.meta import commands
from motata_cli.meta.services.migrate import run_migration_flow
from motata_cli.meta.services.migration_state import (
    MigrationState, MigrationStateError, atomic_json, job_lock, load_job,
)


class MigrationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.args = argparse.Namespace(
            export_dir=str(self.root), source_account_id="123", target_account_id="456",
            page_id="789", pixel_id="101", instagram_user_id=None,
            target_application_id=None, target_object_store_url=None,
            promoted_object_overrides_json=None, reuse_existing_by_name=True,
            source_access_token="DO_NOT_SAVE_SOURCE", target_access_token="DO_NOT_SAVE_TARGET", job_id="test",
        )
        self.tree = {"tree": [{"campaign": {"id": "c1", "name": "same", "objective": "OUTCOME_TRAFFIC"}, "adsets": [
            {"adset": {"id": "s1", "name": "same", "optimization_goal": "LINK_CLICKS", "billing_event": "IMPRESSIONS", "targeting": {}}, "ads": []}
        ]}]}
        self.patches = [
            patch.object(commands, "JOBS_DIR", self.root / "jobs"),
            patch.object(commands, "load_required_migration_export", side_effect=lambda _: (self.tree, {})),
            patch.object(commands, "build_auth_from_args", return_value=argparse.Namespace(access_token="fake")),
            patch.object(commands, "MetaClient"),
            patch.object(commands, "validate_migration_preflight"),
            patch.object(commands, "create_campaign", return_value={"id": "new-c"}),
            patch.object(commands, "create_adset", return_value={"id": "new-s"}),
            patch.object(commands, "cleanup_object"),
        ]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.path = self.root / "jobs/test.json"

    def run_job(self):
        return run_migration_flow(self.args, commands_module=commands)

    def test_completed_repeat_skips_every_remote_call_and_names_never_reuse(self):
        first = self.run_job()
        calls = commands.MetaClient.call_count
        second = self.run_job()
        self.assertEqual(first["old_to_new_campaigns"], second["old_to_new_campaigns"])
        commands.create_campaign.assert_called_once()
        commands.create_adset.assert_called_once()
        self.assertEqual(commands.MetaClient.call_count, calls)
        self.assertEqual(commands.create_campaign.call_args.args[2].status, "PAUSED")
        self.assertEqual(commands.create_adset.call_args.args[2].status, "PAUSED")
        self.assertNotIn("DO_NOT_SAVE", self.path.read_text())
        commands.cleanup_object.assert_not_called()

    def test_timeout_records_pending_before_write_and_never_retries(self):
        def fail(*args):
            job = load_job(self.path)
            self.assertEqual(job["ledger"]["adset:s1"]["status"], "pending")
            raise TimeoutError("secret must not be saved")
        commands.create_adset.side_effect = fail
        with self.assertRaises(commands.CliError):
            self.run_job()
        job = load_job(self.path)
        self.assertEqual(job["status"], "needs_review")
        self.assertEqual(job["ledger"]["campaign:c1"]["target_id"], "new-c")
        self.assertEqual(job["cleanup_plan"]["mode"], "plan_only")
        with self.assertRaises(commands.CliError):
            self.run_job()
        commands.create_adset.assert_called_once()
        commands.cleanup_object.assert_not_called()
        self.assertNotIn("secret", self.path.read_text())

    def test_interruption_between_writes_resumes_confirmed_parent(self):
        original = MigrationState.write
        def interrupt(state, kind, *args, **kwargs):
            if kind == "adset":
                raise KeyboardInterrupt()
            return original(state, kind, *args, **kwargs)
        with patch.object(MigrationState, "write", interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_job()
        self.assertEqual(load_job(self.path)["status"], "failed")
        self.run_job()
        commands.create_campaign.assert_called_once()
        commands.create_adset.assert_called_once()

    def test_interrupt_during_write_is_uncertain(self):
        commands.create_campaign.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_job()
        self.assertEqual(load_job(self.path)["status"], "needs_review")
        with self.assertRaises(commands.CliError):
            self.run_job()
        commands.create_campaign.assert_called_once()

    def test_failure_before_first_write_is_saved_and_resumable(self):
        commands.validate_migration_preflight.side_effect = ValueError("bad")
        with self.assertRaises(commands.CliError):
            self.run_job()
        self.assertEqual(load_job(self.path)["ledger"], {})
        commands.validate_migration_preflight.side_effect = None
        self.assertEqual(self.run_job()["status"], "completed")

    def test_export_drift_blocks_resume(self):
        self.run_job()
        self.tree["tree"][0]["campaign"]["name"] = "changed"
        with self.assertRaises(commands.CliError):
            self.run_job()
        commands.create_campaign.assert_called_once()

    def test_corrupt_and_legacy_jobs_fail_closed(self):
        self.path.parent.mkdir()
        for content in ("{broken", "{}", '{"schema_version":1}', '{"schema_version":99}'):
            self.path.write_text(content)
            with self.assertRaises(MigrationStateError):
                self.run_job()
        commands.MetaClient.assert_not_called()

    def test_same_job_lock_blocks_writer(self):
        with job_lock(self.path):
            with self.assertRaises(MigrationStateError):
                self.run_job()
        commands.MetaClient.assert_not_called()
        self.run_job()

    def test_atomic_replace_failure_preserves_old_checkpoint(self):
        atomic_json(self.path, {"old": True})
        with patch("motata_cli.meta.services.migration_state.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                atomic_json(self.path, {"old": False})
        self.assertEqual(json.loads(self.path.read_text()), {"old": True})
        self.assertEqual(list(self.path.parent.glob(".test.json.*")), [])

    def test_pending_checkpoint_failure_prevents_remote_write(self):
        original = MigrationState.save
        def fail(state):
            if state.data["ledger"]:
                raise OSError("disk full")
            return original(state)
        with patch.object(MigrationState, "save", fail):
            with self.assertRaises(OSError):
                self.run_job()
        commands.create_campaign.assert_not_called()

    def test_discovered_page_does_not_change_job_config(self):
        self.args.page_id = None
        with patch.object(commands, "infer_promotable_pages", return_value={"pages": [
            {"page_id": "discovered", "ad_attach_usable": True, "token_visible": True}
        ]}) as discover:
            self.run_job()
            self.run_job()
        discover.assert_called_once()
        job = load_job(self.path)
        self.assertIsNone(job["config"]["page_id"])
        self.assertEqual(job["resolved_page_id"], "discovered")

    def test_confirmation_disk_failure_leaves_pending_and_blocks_retry(self):
        original = MigrationState.save
        def fail(state):
            if state.data["ledger"].get("campaign:c1", {}).get("status") in {"confirmed", "needs_review"}:
                raise OSError("disk full")
            return original(state)
        with patch.object(MigrationState, "save", fail):
            with self.assertRaises(OSError):
                self.run_job()
        self.assertEqual(load_job(self.path)["status"], "needs_review")
        self.assertEqual(load_job(self.path)["ledger"]["campaign:c1"]["status"], "pending")
        with self.assertRaises(commands.CliError):
            self.run_job()
        commands.create_campaign.assert_called_once()
        commands.create_adset.assert_not_called()

    def test_structurally_corrupt_ledger_refused(self):
        self.run_job()
        original = load_job(self.path)
        for entry in ([], None, {"kind": "ad", "source_id": "x", "request_fingerprint": "bad"}):
            data = {**original, "ledger": {"ad:x": entry}}
            atomic_json(self.path, data)
            with self.assertRaises(MigrationStateError):
                load_job(self.path)

    def test_lock_is_cross_process(self):
        code = '''
import sys
from pathlib import Path
from motata_cli.meta.services.migration_state import job_lock, MigrationStateError
try:
    with job_lock(Path(sys.argv[1])):
        pass
except MigrationStateError:
    sys.exit(3)
'''
        def contender(path):
            return subprocess.run(
                [sys.executable, "-c", code, str(path)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True, timeout=10,
            )

        with job_lock(self.path):
            result = contender(self.path)
            self.assertEqual(result.returncode, 3, result.stderr)
            independent = contender(self.path.with_name("independent.json"))
            self.assertEqual(independent.returncode, 0, independent.stderr)
        released = contender(self.path)
        self.assertEqual(released.returncode, 0, released.stderr)

    def test_cli_resume_uses_checkpoint_and_does_not_repeat_writes(self):
        self.run_job()
        with patch.object(commands, "print_output") as output:
            commands.command_migrate_resume(argparse.Namespace(
                job_id="test", source_access_token="replacement", target_access_token="replacement",
            ))
        self.assertEqual(output.call_args.args[0]["status"], "completed")
        commands.create_campaign.assert_called_once()
        commands.create_adset.assert_called_once()

    def test_invalid_job_id_is_rejected(self):
        self.args.job_id = "../outside"
        with self.assertRaises(MigrationStateError):
            self.run_job()
        commands.MetaClient.assert_not_called()

    def test_resume_cannot_create_missing_job(self):
        self.args._resume = True
        with self.assertRaises(MigrationStateError):
            self.run_job()
        commands.MetaClient.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_distinct_source_ids_with_same_name_are_both_created(self):
        self.tree["tree"].append({"campaign": {"id": "c2", "name": "same", "objective": "OUTCOME_TRAFFIC"}, "adsets": []})
        commands.create_campaign.side_effect = [{"id": "new-c"}, {"id": "new-c2"}]
        result = self.run_job()
        self.assertEqual(result["old_to_new_campaigns"], {"c1": "new-c", "c2": "new-c2"})
        self.run_job()
        self.assertEqual(commands.create_campaign.call_count, 2)

    def test_missing_remote_id_needs_review(self):
        commands.create_campaign.return_value = {}
        with self.assertRaises(commands.CliError):
            self.run_job()
        self.assertEqual(load_job(self.path)["status"], "needs_review")

    def test_missing_referenced_creative_blocks_before_any_write(self):
        self.tree["tree"][0]["adsets"][0]["ads"] = [
            {"ad": {"id": "a1", "name": "ad"}, "creative": {"id": "missing"}}
        ]
        with self.assertRaises(commands.CliError):
            self.run_job()
        self.assertEqual(load_job(self.path)["ledger"], {})
        commands.create_campaign.assert_not_called()
        commands.create_adset.assert_not_called()

    def test_mismatched_creative_identity_blocks_before_any_write(self):
        self.tree["tree"][0]["adsets"][0]["ads"] = [
            {"ad": {"id": "a1", "name": "ad"}, "creative": {"id": "cr1"}}
        ]
        commands.load_required_migration_export.side_effect = lambda _: (
            self.tree, {"cr1": {"id": "cr2", "name": "creative", "object_story_id": "post"}},
        )
        with self.assertRaises(commands.CliError):
            self.run_job()
        self.assertEqual(load_job(self.path)["ledger"], {})
        commands.create_campaign.assert_not_called()
        commands.create_adset.assert_not_called()


if __name__ == "__main__":
    unittest.main()
