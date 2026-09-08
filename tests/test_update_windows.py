from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from motata_cli import update


class WindowsUpdateCommandTests(unittest.TestCase):
    def test_npm_wrappers_use_node_without_a_command_shell(self):
        with tempfile.TemporaryDirectory(prefix="motata npm ") as directory:
            root = Path(directory)
            (root / "node.exe").write_text("")
            for name in ("npm", "npx"):
                wrapper = root / f"{name}.cmd"
                script = root / "node_modules/npm/bin" / f"{name}-cli.js"
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text("")
                command = [name, "add", "https://example.test/a?b=1&c=2", "--skill", "*"]
                with self.subTest(name=name), patch.object(update.sys, "platform", "win32"), \
                     patch.object(update.shutil, "which", return_value=str(wrapper)), \
                     patch.object(update.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                    self.assertTrue(update._run(command).ok)
                    self.assertEqual(run.call_args.args[0], [str(root / "node.exe"), str(script), *command[1:]])
                    self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_missing_js_entry_does_not_fall_back_to_batch_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(update.sys, "platform", "win32"), \
                 patch.object(update.shutil, "which", return_value=str(Path(directory) / "npx.cmd")), \
                 patch.object(update.subprocess, "run") as run:
                self.assertFalse(update._run(["npx", "add", "https://example.test"]).ok)
                run.assert_not_called()

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the local argv smoke")
    def test_real_node_preserves_registry_arguments(self):
        node = shutil.which("node")
        with tempfile.TemporaryDirectory(prefix="motata npm ") as directory:
            root = Path(directory)
            script = root / "node_modules/npm/bin/npx-cli.js"
            script.parent.mkdir(parents=True)
            script.write_text("process.stdout.write(JSON.stringify(process.argv.slice(2)));")
            args = ["add", 'https://example.test/a?x=1&y=%PATH%|echo injected', 'space and "quotes"', "*"]
            def which(name):
                return str(root / "npx.cmd") if name == "npx" else node
            with patch.object(update.sys, "platform", "win32"), patch.object(update.shutil, "which", side_effect=which):
                result = update._run(["npx", *args])
            self.assertTrue(result.ok, result.stderr)
            self.assertEqual(json.loads(result.stdout), args)


if __name__ == "__main__":
    unittest.main()
