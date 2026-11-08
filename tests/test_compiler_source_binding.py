"""A compiler cannot attest a source tree different from its actual imports."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared_player import compiler

ROOT = Path(__file__).resolve().parents[1]


class CompilerSourceBindingTests(unittest.TestCase):
    def setUp(self):
        from shared_player.inputs import source_manifest

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manifest = Path(self.directory.name) / "source-manifest.json"
        self.manifest.write_text(json.dumps(source_manifest(ROOT)))

    def guard(self):
        method = getattr(compiler, "verify_running_source", None)
        self.assertTrue(callable(method), "actual running-source preflight required")
        return method

    def test_actual_registered_source_and_imports_are_hash_bound(self):
        proof = self.guard()(self.manifest)
        self.assertEqual(proof["compiler_path"], str(Path(compiler.__file__).resolve()))
        self.assertGreaterEqual(len(proof["shared_modules"]), 5)

    def test_another_snapshot_cannot_attest_this_compiler_even_with_declared_bytes(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source-manifest.json"
            path.write_text(json.dumps({"files": {}}))
            with self.assertRaisesRegex(
                compiler.CompilerError, "loaded shared module source hash differs"
            ):
                self.guard()(path)

    def test_as3_and_reference_roots_cannot_be_borrowed_from_old_snapshot(self):
        for field in ("AS3_ROOT", "REFERENCE"):
            with (
                self.subTest(field=field),
                patch.object(compiler, field, Path("/wrong/snapshot")),
            ):
                with self.assertRaisesRegex(
                    compiler.CompilerError, "running compiler source roots differ"
                ):
                    self.guard()(self.manifest)


if __name__ == "__main__":
    unittest.main()
