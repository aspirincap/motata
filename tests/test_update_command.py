from __future__ import annotations

import io
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from motata_cli import __version__
from motata_cli.__main__ import build_parser
from motata_cli import update as update_module

DEFAULT_SOURCE = "https://skill.motata.one"
LEGACY_SOURCE = "https://motata-skills.pages.dev"


class UpdateCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict("os.environ", {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden in unit tests"))
        network.start()
        self.addCleanup(network.stop)
        # Block alternative network paths and subprocesses (npm/npx/pip) too.
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "subprocess.run"):
            guard = patch(target, side_effect=AssertionError("external I/O forbidden in update unit tests"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_npm_launcher_detected_from_installed_site_packages(self) -> None:
        with patch.dict("os.environ", {"MOTATA_INSTALL_METHOD": "npm"}):
            self.assertEqual(update_module.detect_install_method(root=Path("/tmp/site-packages")), "npm")

    def test_unknown_channel_does_not_override_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MOTATA_INSTALL_METHOD": "unknown"}):
                self.assertEqual(update_module.detect_install_method(root=Path(tmp)), "pip")

    def test_runtime_python_symlink_keeps_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}")
            python_path = root / ".runtime/venv/bin/python"
            python_path.parent.mkdir(parents=True)
            python_path.symlink_to(__import__("sys").executable)
            self.assertEqual(update_module.detect_install_method(root=root, python_executable=str(python_path)), "npm")

    def test_update_command_is_exposed(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["update", "--skills-only"])

        self.assertIs(args.func, update_module.command_update)
        self.assertTrue(args.skills_only)
        self.assertFalse(args.cli_only)
        self.assertEqual(args.skills_source, update_module.SKILLS_SOURCE_DEFAULT)

    def test_update_scope_flags_are_mutually_exclusive(self) -> None:
        parser = build_parser()

        with self.assertRaises(SystemExit):
            parser.parse_args(["update", "--cli-only", "--skills-only"])

    def test_detect_install_method_returns_npm_for_runtime_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            python_path = root / ".runtime" / "venv" / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")

            self.assertEqual(
                update_module.detect_install_method(root=root, python_executable=str(python_path)),
                "npm",
            )

    def test_detect_install_method_returns_source_for_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text("{}", encoding="utf-8")
            python_path = root / "venv" / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")

            self.assertEqual(
                update_module.detect_install_method(root=root, python_executable=str(python_path)),
                "source",
            )

    def test_detect_install_method_returns_pip_without_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            python_path = root / "venv" / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("", encoding="utf-8")

            self.assertEqual(
                update_module.detect_install_method(root=root, python_executable=str(python_path)),
                "pip",
            )

    def test_skills_stamp_current_requires_matching_version_and_source(self) -> None:
        stamp = {
            "cli_version": "0.1.6",
            "skills_source": DEFAULT_SOURCE,
        }

        self.assertTrue(
            update_module.skills_stamp_is_current(
                stamp,
                cli_version="0.1.6",
                skills_source=DEFAULT_SOURCE,
            )
        )
        self.assertFalse(
            update_module.skills_stamp_is_current(
                stamp,
                cli_version="0.1.7",
                skills_source=DEFAULT_SOURCE,
            )
        )
        self.assertFalse(
            update_module.skills_stamp_is_current(
                stamp,
                cli_version="0.1.6",
                skills_source="https://example.com",
            )
        )

    def test_build_skills_sync_command_targets_motata_registry(self) -> None:
        command = update_module.build_skills_sync_command(skills_source=DEFAULT_SOURCE)

        self.assertEqual(
            command,
            [
                "npx",
                "-y",
                "skills",
                "add",
                DEFAULT_SOURCE,
                "--skill",
                "*",
                "-g",
                "-y",
            ],
        )

    def test_compatibility_release_for_current_version(self) -> None:
        release = update_module.compatibility_release_for(
            __version__,
            skills_source=DEFAULT_SOURCE,
            prefer_remote=False,
        )

        self.assertIsNotNone(release)
        self.assertEqual(release["bundle_id"], "motata-skills-2026-09-08")
        self.assertIn("motata-report", release["required_skills"])

    def test_compatibility_release_supports_legacy_alias(self) -> None:
        release = update_module.compatibility_release_for(
            __version__,
            skills_source=LEGACY_SOURCE,
            prefer_remote=False,
        )

        self.assertIsNotNone(release)
        self.assertEqual(release["bundle_id"], "motata-skills-2026-09-08")

    def test_compatibility_manifest_url_uses_well_known_endpoint(self) -> None:
        url = update_module.compatibility_manifest_url(DEFAULT_SOURCE)

        self.assertEqual(
            url,
            "https://skill.motata.one/.well-known/motata/skills-compatibility.json",
        )

    def test_remote_manifest_can_override_local_release(self) -> None:
        original_fetch = update_module.fetch_remote_compatibility_manifest
        update_module.fetch_remote_compatibility_manifest = lambda source: {
            "releases": [
                {
                    "cli_version": "0.1.6",
                    "bundle_id": "remote-bundle",
                    "skills_source": DEFAULT_SOURCE,
                    "required_skills": ["motata-starter"],
                }
            ]
        }
        try:
            release = update_module.compatibility_release_for(
                "0.1.6",
                skills_source=DEFAULT_SOURCE,
            )
        finally:
            update_module.fetch_remote_compatibility_manifest = original_fetch

        self.assertIsNotNone(release)
        self.assertEqual(release["bundle_id"], "remote-bundle")

    def test_remote_manifest_falls_back_to_local_when_alias_not_published_yet(self) -> None:
        original_fetch = update_module.fetch_remote_compatibility_manifest
        update_module.fetch_remote_compatibility_manifest = lambda source: {
            "releases": [
                {
                    "cli_version": __version__,
                    "bundle_id": "remote-legacy-only",
                    "skills_source": LEGACY_SOURCE,
                    "required_skills": ["motata-starter"],
                }
            ]
        }
        try:
            release = update_module.compatibility_release_for(
                __version__,
                skills_source=DEFAULT_SOURCE,
            )
        finally:
            update_module.fetch_remote_compatibility_manifest = original_fetch

        self.assertIsNotNone(release)
        self.assertEqual(release["bundle_id"], "motata-skills-2026-09-08")

    def test_fetch_remote_manifest_sends_user_agent(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, limit: int) -> bytes:
                captured["read_limit"] = limit
                return (
                    b'{"releases":[{"cli_version":"0.1.6","skills_source":"https://skill.motata.one"}]}'
                )

        captured = {}
        original_urlopen = update_module.request.urlopen

        def fake_urlopen(req, timeout=10):
            captured["user_agent"] = req.headers.get("User-agent")
            captured["accept"] = req.headers.get("Accept")
            return FakeResponse()

        update_module.request.urlopen = fake_urlopen
        try:
            payload = update_module.fetch_remote_compatibility_manifest(
                DEFAULT_SOURCE
            )
        finally:
            update_module.request.urlopen = original_urlopen

        self.assertIsNotNone(payload)
        self.assertEqual(captured["user_agent"], f"motata-cli/{__version__}")
        self.assertEqual(captured["accept"], "application/json")
        self.assertEqual(captured["read_limit"], 2_000_001)

    def test_build_skills_drift_notice_when_stamp_missing(self) -> None:
        notice = update_module.build_skills_drift_notice(
            cli_version="0.1.6",
            skills_source=DEFAULT_SOURCE,
            stamp=None,
            command_name="meta",
        )

        self.assertIn("motata update --skills-only", notice or "")
        self.assertIn("0.1.6", notice or "")

    def test_build_skills_drift_notice_omits_update_command(self) -> None:
        notice = update_module.build_skills_drift_notice(
            cli_version="0.1.6",
            skills_source=DEFAULT_SOURCE,
            stamp=None,
            command_name="update",
        )

        self.assertIsNone(notice)

    def test_summarize_update_result_reports_skills_synced(self) -> None:
        summary = update_module.summarize_update_result(
            install_method="source",
            cli_result=None,
            skills_result={"status": "synced", "message": "Motata skills synced."},
            current_version="0.1.6",
            skills_source=DEFAULT_SOURCE,
        )

        self.assertEqual(summary["action"], "skills_synced")
        self.assertEqual(summary["skills_action"], "synced")
        self.assertIn("skills synced", summary["message"].lower())

    def test_maybe_emit_skills_drift_notice_writes_to_tty_stream(self) -> None:
        class FakeTTY(io.StringIO):
            def isatty(self) -> bool:
                return True

        stream = FakeTTY()
        original_loader = update_module.load_skills_stamp
        original_registry = update_module.build_skills_registry_status
        update_module.load_skills_stamp = lambda: None
        update_module.build_skills_registry_status = lambda **kwargs: {
            "missing_required_skills": ["motata-report"]
        }
        try:
            update_module.maybe_emit_skills_drift_notice(
                command_name="meta",
                cli_version="0.1.6",
                skills_source=DEFAULT_SOURCE,
                stream=stream,
            )
        finally:
            update_module.load_skills_stamp = original_loader
            update_module.build_skills_registry_status = original_registry

        self.assertIn("motata update --skills-only", stream.getvalue())
        self.assertIn("motata-report", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
