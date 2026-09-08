from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("skill_registry_build", ROOT / "scripts" / "build_skill_registry.py")
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)


class SkillRegistryTests(unittest.TestCase):
    def test_single_canonical_source_and_content_hashes(self):
        index = registry.build_agent_skills_index()
        self.assertEqual(len(index["skills"]), 5)
        for skill in index["skills"]:
            source = registry.resolve_skill_source(skill["name"])
            self.assertTrue(source.is_relative_to(ROOT / "registry" / "skills"))
            self.assertFalse(source.is_symlink())
            self.assertEqual(set(skill["files"]), set(skill["sha256"]))
            for name, digest in skill["sha256"].items():
                self.assertEqual(digest, hashlib.sha256((source / name).read_bytes()).hexdigest())

    def test_build_uses_only_canonical_files(self):
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory)
            skills = public / ".well-known" / "agent-skills"
            with patch.multiple(registry, PUBLIC_ROOT=public, AGENT_SKILLS_ROOT=skills,
                                MOTATA_WELL_KNOWN_ROOT=public / ".well-known" / "motata"):
                registry.build_registry()
                (skills / "stale.txt").write_text("stale")
                result = registry.build_registry()
                self.assertEqual(result["skill_count"], 5)
                self.assertFalse((skills / "stale.txt").exists())
                self.assertTrue((skills / "motata-report" / "SKILL.md").is_file())

    def test_rejects_symlinks_unreviewed_files_and_secret_signatures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("safe")
            link = root / "outside.md"
            link.symlink_to(root / "SKILL.md")
            with self.assertRaisesRegex(ValueError, "Symlinks"):
                registry.publishable_files(root)
            link.unlink()
            private = root / ".env"
            private.write_text("not-a-real-secret")
            with self.assertRaisesRegex(ValueError, "Unreviewed"):
                registry.publishable_files(root)
            private.unlink()
            (root / "bad.md").write_text("npm_" + "A" * 36)
            with self.assertRaisesRegex(ValueError, "Potential secret"):
                registry.publishable_files(root)

    def test_validation_precedes_output_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory)
            (public / "keep.txt").write_text("old-valid-build")
            with patch.object(registry, "PUBLIC_ROOT", public), patch.object(
                registry, "build_agent_skills_index", side_effect=ValueError("bad source")
            ):
                with self.assertRaises(ValueError):
                    registry.build_registry()
            self.assertEqual((public / "keep.txt").read_text(), "old-valid-build")

    def test_rejects_hidden_and_runtime_directories_and_case_variants(self):
        for relative in (".private/data.json", "runtime/data.json", "outputs/data.md",
                         "node_modules/package.json", "references/CREDENTIALS.JSON",
                         "references/Auth.json"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "SKILL.md").write_text("safe")
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("safe-looking-content")
                with self.assertRaisesRegex(ValueError, "Unreviewed"):
                    registry.publishable_files(root)


if __name__ == "__main__":
    unittest.main()
