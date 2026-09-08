"""Offline regression coverage for shared helpers and CLI compatibility seams."""
import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from motata_cli.common import auth, config, utils
from motata_cli.common.errors import CliError
from motata_cli.meta import commands
from motata_cli.meta.services import media


class CommonLayerTests(unittest.TestCase):
    def test_compatible_exports_and_error_code(self):
        self.assertIs(commands.CliError, CliError)
        self.assertIs(commands.AuthContext, auth.AuthContext)
        self.assertIs(commands.env_first, utils.env_first)
        self.assertIs(commands.parse_json_option, utils.parse_json_option)
        self.assertEqual(CliError("failed").exit_code, 1)
        self.assertEqual(CliError("failed", exit_code=3).exit_code, 3)
        self.assertEqual(str(CliError("failed")), "failed")

    def test_auth_is_direct_only(self):
        resolved = auth.resolve_auth(account_id="act_123", access_token="mock-token")
        self.assertEqual(resolved.account_id, "123")
        self.assertEqual(resolved.source, "direct")
        with self.assertRaises(CliError):
            auth.resolve_auth(account_id="123")

    def test_json_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "test.json"
            self.assertEqual(utils.load_json_file(path, default={}), {})
            utils.write_json_file(path, {"name": "测试"})
            self.assertEqual(utils.load_json_file(path), {"name": "测试"})
        with self.assertRaises(CliError):
            utils.parse_json_option("{", "payload")
        with self.assertRaises(CliError):
            utils.parse_json_option("[]", "payload", dict)
        with self.assertRaises(CliError):
            utils.parse_positive_int(0, "budget")

    def test_config_and_legacy_patch_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            with patch.object(commands, "CACHE_DIR", home), patch.object(commands, "JOBS_DIR", home / "jobs"), patch.object(commands, "CONFIG_PATH", home / "config.json"):
                commands.save_config({"values": {"default_account": "act_123"}, "account_aliases": {"demo": {"account_id": "456"}}})
                self.assertEqual(commands.resolve_account_ref(None), "123")
                self.assertEqual(commands.resolve_account_ref("demo"), "456")
                self.assertTrue((home / "jobs").is_dir())
        self.assertEqual(config.resolve_account_ref("demo", config_loader=lambda: {"account_aliases": {"demo": "act_789"}}), "789")

    def test_media_compatibility_injects_dependencies(self):
        with patch.object(commands, "pick_link", return_value="https://example.test") as picker:
            self.assertEqual(commands.detect_creative_migration_mode({}), "link")
            picker.assert_called_once_with({})
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = [b"one", b"", b"two"]
        with tempfile.TemporaryDirectory() as directory, patch.object(commands.requests, "get", return_value=response) as get:
            dest = Path(directory) / "image.jpg"
            commands.download_file("https://example.test/image.jpg", dest)
            self.assertEqual(dest.read_bytes(), b"onetwo")
            get.assert_called_once_with("https://example.test/image.jpg", stream=True, timeout=300)
        self.assertIs(commands.pick_link, media.pick_link)

    def test_lower_layers_do_not_import_commands(self):
        root = Path(__file__).resolve().parents[1] / "motata_cli"
        paths = [root / "common" / f"{name}.py" for name in ("errors", "auth", "utils", "config")]
        paths += [root / "meta" / name for name in ("payloads.py", "preflight.py", "services/resources.py", "services/creatives.py", "services/media.py")]
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("commands", node.module or "", str(path))
                    self.assertNotIn("commands", [alias.name for alias in node.names], str(path))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("commands", alias.name, str(path))


if __name__ == "__main__":
    unittest.main()
