"""One compiled helper inventory must preserve payload, profile and contracts."""

import json
import tempfile
import unittest
from pathlib import Path

from test_shared_player_bundle import (
    policy_only_controller,
    synthetic_catalog,
    synthetic_export,
)


def helpers(root):
    from shared_player.bundle import create_bundle, materialize
    from shared_player.sources import AS3_ROOT, build_sources

    exported = synthetic_export(root / "export")
    create_bundle(exported, root / "bundle", controller=policy_only_controller())
    catalog = root / "catalog.json"
    catalog.write_text(json.dumps(synthetic_catalog()))
    prepared = materialize(root / "bundle", catalog, root / "prepared")
    sources = build_sources(prepared)
    sources["GwaiCapture"] = (AS3_ROOT / "GwaiCapture.as").read_text()
    return sources, prepared.metadata


class SharedCompilerTests(unittest.TestCase):
    def test_one_model_and_contract_constants_pass_without_vm_claim(self):
        from shared_player.compiler import verify_compiled_helpers

        with tempfile.TemporaryDirectory() as directory:
            sources, metadata = helpers(Path(directory))
            result = verify_compiled_helpers(sources, metadata)
            self.assertEqual(result["model_count"], 1)
            self.assertTrue(result["embedded_payload_verified"])
            self.assertFalse(result["vm_execution_verified"])

    def test_changed_compiled_payload_or_counter_contract_is_refused(self):
        from shared_player.compiler import CompilerError, verify_compiled_helpers

        with tempfile.TemporaryDirectory() as directory:
            sources, metadata = helpers(Path(directory))
            weights = next(
                name for name in sources if name.startswith("GwaiNetWeightsData_")
            )
            corrupted = dict(sources)
            corrupted[weights] = corrupted[weights].replace(
                metadata["weights_sha256"], "f" * 64
            )
            with self.assertRaises(CompilerError):
                verify_compiled_helpers(corrupted, metadata)
            sources["GwaiSharedConfig"] = sources["GwaiSharedConfig"].replace(
                "completed-primary-turns-v1", "accepted-actions"
            )
            with self.assertRaises(CompilerError):
                verify_compiled_helpers(sources, metadata)

    def test_extra_obsolete_class_or_missing_value_helper_is_refused(self):
        from shared_player.compiler import CompilerError, verify_compiled_helpers

        with tempfile.TemporaryDirectory() as directory:
            sources, metadata = helpers(Path(directory))
            extra = {
                **sources,
                "GwaiNetTierConfig": "internal class GwaiNetTierConfig {}",
            }
            with self.assertRaises(CompilerError):
                verify_compiled_helpers(extra, metadata)
            del sources["GwaiNetPolicy"]
            with self.assertRaises(CompilerError):
                verify_compiled_helpers(sources, metadata)


if __name__ == "__main__":
    unittest.main()
