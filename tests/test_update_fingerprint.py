from __future__ import annotations

import argparse
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from motata_cli import update as u


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for guard in (
            patch.dict(os.environ, {"MOTATA_HOME": self.tmp.name}),
            patch.object(u.request, "urlopen", side_effect=AssertionError("network forbidden")),
            patch.object(u.subprocess, "run", side_effect=AssertionError("subprocess forbidden")),
            patch.object(u, "_executable_command", side_effect=lambda command: command),
        ):
            guard.start()
            self.addCleanup(guard.stop)
        self.index = {"skills": [{"name": "motata-report", "files": ["SKILL.md"], "sha256": {"SKILL.md": "a" * 64}}]}

    def status(self, index, stamp):
        with patch.object(u, "fetch_remote_skills_index", return_value=index), \
             patch.object(u, "load_skills_stamp", return_value=stamp), \
             patch.object(u, "detect_install_method", return_value="pip"), \
             patch.object(u, "get_latest_pypi_version", return_value=None), \
             patch.object(u, "load_effective_compatibility_manifest", return_value=({}, "local")), \
             patch.object(u, "build_skills_registry_status", return_value={}):
            return u.build_update_status(skills_source=u.SKILLS_SOURCE_DEFAULT)

    def stamp(self):
        return {"cli_version": u.__version__, "skills_source": u.SKILLS_SOURCE_DEFAULT,
                "content_fingerprint": u.skills_index_fingerprint(self.index)}

    def test_same_version_content_drift(self):
        stamp = self.stamp()
        self.index["skills"][0]["sha256"]["SKILL.md"] = "b" * 64
        status = self.status(self.index, stamp)
        self.assertIs(status["skills_snapshot_in_sync"], False)
        self.assertIs(status["skills_in_sync"], False)

    def test_matching_snapshot_does_not_claim_installed_bytes(self):
        status = self.status(self.index, self.stamp())
        self.assertIs(status["skills_snapshot_in_sync"], True)
        self.assertIsNone(status["skills_in_sync"])
        self.assertIsNone(status["installed_content_verified"])
        self.assertEqual(status["verification_scope"], "registry_snapshot_only")

    def test_unavailable_and_legacy_are_unknown(self):
        self.assertIsNone(self.status(None, self.stamp())["skills_in_sync"])
        self.assertIsNone(self.status(self.index, {"cli_version": u.__version__})["skills_snapshot_in_sync"])

    def test_fingerprint_ignores_metadata_and_order(self):
        changed = copy.deepcopy(self.index)
        changed["skills"][0]["description"] = "metadata only"
        self.assertEqual(u.skills_index_fingerprint(self.index), u.skills_index_fingerprint(changed))

    def test_reject_invalid_and_secret_paths_without_opening_files(self):
        for filename in ("../SKILL.md", "/SKILL.md", "ref/../../auth.json", ".env", "credentials.json", "runtime/a.json", "outputs/a.md", "a\\b.md"):
            index = copy.deepcopy(self.index)
            index["skills"][0]["files"].append(filename)
            index["skills"][0]["sha256"][filename] = "b" * 64
            with patch.object(Path, "read_bytes", side_effect=AssertionError("must not read installation")):
                self.assertIsNone(u.skills_index_fingerprint(index), filename)
        for index in (None, {}, {"skills": []}, {"skills": [None]}):
            self.assertIsNone(u.skills_index_fingerprint(index))

    def test_snapshot_never_follows_installed_symlinks(self):
        installed = Path(self.tmp.name) / "installed"
        installed.mkdir()
        (installed / "SKILL.md").symlink_to(installed / "credentials.json")
        with patch.object(Path, "read_bytes", side_effect=AssertionError("installed bytes forbidden")):
            self.assertIsNotNone(u.skills_index_fingerprint(self.index))

    def test_fetch_offline_timeout_invalid_and_bounded(self):
        for exc in (URLError("offline"), TimeoutError(), OSError()):
            with patch.object(u.request, "urlopen", side_effect=exc) as fetch:
                self.assertIsNone(u.fetch_remote_skills_index(u.SKILLS_SOURCE_DEFAULT))
                self.assertEqual(fetch.call_args.kwargs["timeout"], 10)
                self.assertEqual(fetch.call_count, 1)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(self.index).encode()
        with patch.object(u.request, "urlopen", return_value=response):
            self.assertEqual(u.fetch_remote_skills_index(u.SKILLS_SOURCE_DEFAULT), self.index)
            response.__enter__.return_value.read.assert_called_once_with(2_000_001)

    def test_stamp_home_and_fingerprint(self):
        with patch.object(u, "fetch_remote_skills_index", return_value=self.index), \
             patch.object(u, "compatibility_release_for", return_value={}):
            path = u.write_skills_stamp(cli_version=u.__version__, skills_source=u.SKILLS_SOURCE_DEFAULT)
        self.assertEqual(path.parent, Path(self.tmp.name))
        self.assertEqual(u.load_skills_stamp()["content_fingerprint"], u.skills_index_fingerprint(self.index))

    def test_malformed_remote_compatibility_uses_local_release(self):
        for releases in ({"bad": "shape"}, [None], [{"cli_version": u.__version__,
                "skills_source": u.SKILLS_SOURCE_DEFAULT, "required_skills": "not-a-list"}]):
            with self.subTest(releases=releases), patch.object(
                u, "fetch_remote_compatibility_manifest", return_value={"releases": releases}
            ):
                release = u.compatibility_release_for(u.__version__, skills_source=u.SKILLS_SOURCE_DEFAULT)
                self.assertIsNotNone(release)
                self.assertIn("motata-report", release["required_skills"])

    def test_remote_compatibility_is_bounded_and_handles_invalid_utf8(self):
        for body in (b"x" * 2_000_001, b"\xff"):
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = body
            with self.subTest(size=len(body)), patch.object(u.request, "urlopen", return_value=response):
                self.assertIsNone(u.fetch_remote_compatibility_manifest(u.SKILLS_SOURCE_DEFAULT))
                response.__enter__.return_value.read.assert_called_once_with(2_000_001)

    def test_notice_only_reads_local_stamp(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        stream = TTY()
        with patch.dict(os.environ, {"MOTATA_SUPPRESS_SKILLS_NOTICE": "0"}), \
             patch.object(u, "build_skills_registry_status", side_effect=AssertionError("not local")):
            u.maybe_emit_skills_drift_notice(command_name="meta", stream=stream)
        self.assertIn("motata update --skills-only", stream.getvalue())

    def test_read_only_inventory_does_not_install(self):
        with patch.object(u.shutil, "which", return_value=None), \
             patch.object(u, "compatibility_release_for", return_value={"required_skills": ["motata-report"]}):
            status = u.build_skills_registry_status(cli_version=u.__version__, skills_source=u.SKILLS_SOURCE_DEFAULT)
        self.assertIsNone(status["compatible"])
        self.assertIsNone(status["missing_required_skills"])
        with patch.object(u.shutil, "which", return_value="/bin/skills"), \
             patch.object(u, "_run", return_value=u.CommandResult(True, [], 0, "[]", "")) as run:
            self.assertEqual(u.list_installed_global_skills(), [])
            self.assertEqual(run.call_args.args[0], ["/bin/skills", "ls", "-g", "--json"])

    def test_run_timeout_no_retry_and_redaction(self):
        with patch.object(u.subprocess, "run", side_effect=subprocess.TimeoutExpired("skills", 120)) as run:
            self.assertFalse(u._run(["skills"]).ok)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.kwargs["timeout"], 120)
        completed = subprocess.CompletedProcess([], 1, "token=secret-one", "Authorization: Bearer secret-two")
        with patch.object(u.subprocess, "run", return_value=completed):
            result = u._run(["skills"])
        self.assertNotIn("secret-one", result.stdout)
        self.assertNotIn("secret-two", result.stderr)

    def test_command_failure_exits_nonzero(self):
        args = argparse.Namespace(skills_source=u.SKILLS_SOURCE_DEFAULT, check=False, skills_only=True, cli_only=False, force=False)
        with patch.object(u, "detect_install_method", return_value="source"), \
             patch.object(u, "perform_skills_sync", return_value={"status": "failed"}), \
             patch("sys.stdout", new_callable=io.StringIO), self.assertRaises(SystemExit) as exc:
            u.command_update(args)
        self.assertEqual(exc.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
