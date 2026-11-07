import tempfile
import unittest
from pathlib import Path

from scripts.check_public_tree import check_paths


class PublicTreeTests(unittest.TestCase):
    def test_source_passes_and_local_material_or_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").write_text("answer = 42\n")
            self.assertEqual(check_paths(root, ["module.py"]), [])
            for name in [
                "AGENTS.md",
                ".private/notes.md",
                "model.pt",
                "games.jsonl",
                "../outside.py",
            ]:
                with self.subTest(name=name):
                    self.assertTrue(check_paths(root, [name]))
            (root / "linked.py").symlink_to(root / "module.py")
            self.assertTrue(check_paths(root, ["linked.py"]))

    def test_private_reference_and_binary_content_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.md").write_text("/" + "home/person/work")
            self.assertTrue(check_paths(root, ["notes.md"]))
            (root / "payload.txt").write_bytes(b"\xff\xfe")
            self.assertTrue(check_paths(root, ["payload.txt"]))
