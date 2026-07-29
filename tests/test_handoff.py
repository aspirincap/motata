from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from motata_cli.handoff import AUTO_END, AUTO_START, sync_handoff_document


class HandoffSyncTests(unittest.TestCase):
    def test_sync_handoff_document_inserts_auto_section_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff_path = root / "RECENT_REFACTOR_HANDOFF.md"
            handoff_path.write_text("# 交接\n\n正文内容。\n", encoding="utf-8")

            touched = root / "motata_cli" / "module.py"
            touched.parent.mkdir(parents=True, exist_ok=True)
            touched.write_text("print('ok')\n", encoding="utf-8")

            ignored = root / "tmp" / "debug.json"
            ignored.parent.mkdir(parents=True, exist_ok=True)
            ignored.write_text("{}", encoding="utf-8")

            content = sync_handoff_document(
                root=root,
                handoff_path=handoff_path,
                note="修复 payload-only TRYON bootstrap 分支判断",
                now=datetime(2026, 4, 22, 17, 30, 0).astimezone(),
            )

            self.assertIn(AUTO_START, content)
            self.assertIn(AUTO_END, content)
            self.assertIn("修复 payload-only TRYON bootstrap 分支判断", content)
            self.assertIn("`motata_cli/module.py`", content)
            self.assertNotIn("`tmp/debug.json`", content)
            self.assertTrue(content.index(AUTO_START) < content.index("# 交接"))

    def test_sync_handoff_document_replaces_existing_auto_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff_path = root / "RECENT_REFACTOR_HANDOFF.md"
            handoff_path.write_text(
                f"{AUTO_START}\n旧内容\n{AUTO_END}\n\n# 交接\n",
                encoding="utf-8",
            )

            touched = root / "README.md"
            touched.write_text("hello\n", encoding="utf-8")

            content = sync_handoff_document(
                root=root,
                handoff_path=handoff_path,
                note="刷新自动维护区",
                now=datetime(2026, 4, 22, 18, 0, 0).astimezone(),
            )

            self.assertEqual(content.count(AUTO_START), 1)
            self.assertEqual(content.count(AUTO_END), 1)
            self.assertIn("刷新自动维护区", content)
            self.assertIn("`README.md`", content)

    def test_sync_handoff_document_ignores_custom_venv_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            handoff_path = root / "RECENT_REFACTOR_HANDOFF.md"

            tracked = root / "motata_cli" / "commands.py"
            tracked.parent.mkdir(parents=True, exist_ok=True)
            tracked.write_text("print('ok')\n", encoding="utf-8")

            ignored = root / ".venv-motata" / "lib" / "python3.14" / "site-packages" / "pkg.py"
            ignored.parent.mkdir(parents=True, exist_ok=True)
            ignored.write_text("print('ignore')\n", encoding="utf-8")

            content = sync_handoff_document(
                root=root,
                handoff_path=handoff_path,
                note="忽略自定义虚拟环境目录",
                now=datetime(2026, 4, 28, 16, 58, 0).astimezone(),
            )

            self.assertIn("`motata_cli/commands.py`", content)
            self.assertNotIn("`.venv-motata/lib/python3.14/site-packages/pkg.py`", content)


if __name__ == "__main__":
    unittest.main()
