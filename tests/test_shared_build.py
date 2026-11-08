"""Public one-model builds preserve identity without claiming training evidence."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared_player.compiler import CompilerError, verify_inference_binding
from shared_player.inputs import source_manifest
from shared_player.sources import shared_numeric_source


class SharedBuildTests(unittest.TestCase):
    def test_inference_receipt_cannot_attest_another_payload_or_training_claim(self):
        metadata = {
            "data_kind": "inference-only",
            "training_manifest_sha256": "unrecorded",
            "checkpoint_sha256": "a" * 64,
            "weights_sha256": "b" * 64,
        }
        receipt = {
            "format": "shared-inference-export-v1",
            "checkpoint_sha256": "a" * 64,
            "payload_sha256": "b" * 64,
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            path.write_text(json.dumps(receipt))
            self.assertEqual(verify_inference_binding(metadata, path), receipt)
            with self.assertRaises(CompilerError):
                verify_inference_binding({**metadata, "weights_sha256": "c" * 64}, path)
            with self.assertRaises(CompilerError):
                verify_inference_binding(
                    {**metadata, "training_manifest_sha256": "d" * 64}, path
                )

    def test_shared_helpers_exclude_historical_tier_dependencies(self):
        source = shared_numeric_source()
        self.assertNotIn("GwaiNetTierConfig", source)
        self.assertNotIn("_lastWinChance", source)
        self.assertIn("function evaluate(", source)
        self.assertIn("function ownedActionUniforms(", source)

    def test_default_cli_routes_one_checkpoint_to_shared_controller(self):
        from scripts import build_mod

        with (
            patch(
                "sys.argv",
                ["build_mod", "--checkpoint", "one.pt", "--output-dir", "new-build"],
            ),
            patch(
                "shared_player.build.build",
                return_value={"status": "offline_compiled_and_export_verified"},
            ) as build,
        ):
            self.assertEqual(build_mod.main(), 0)
            self.assertEqual(build.call_args.kwargs["checkpoint"], Path("one.pt"))
            self.assertIsNone(build.call_args.kwargs["policy_bundle"])

    def test_direct_cli_resolves_shared_package_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            env = {
                key: value for key, value in os.environ.items() if key != "PYTHONPATH"
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(root / "scripts/build_mod.py"),
                    "--policy-bundle",
                    str(directory / "missing-bundle"),
                    "--output-dir",
                    str(directory / "build"),
                ],
                cwd=directory,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("ModuleNotFoundError", result.stderr)
            self.assertIn("bundle directory is missing or symlinked", result.stderr)

    def test_audited_shared_model_selects_the_training_profile(self):
        from scripts.policy_format import FACTION_UNKNOWN_CHECKPOINTS
        from shared_player.build import shared_observation_profile

        for digest in FACTION_UNKNOWN_CHECKPOINTS:
            self.assertEqual(shared_observation_profile(digest), "faction-unknown-v1")
            with self.assertRaisesRegex(ValueError, "cannot be changed"):
                shared_observation_profile(digest, requested="full-faction-v1")
            with self.assertRaisesRegex(ValueError, "independent probes"):
                shared_observation_profile(digest, declared="full-faction-v1")
            self.assertEqual(
                shared_observation_profile(digest, declared="faction-unknown-v1"),
                "faction-unknown-v1",
            )

    def test_other_models_retain_their_declared_profile(self):
        from shared_player.build import shared_observation_profile

        self.assertIsNone(shared_observation_profile("f" * 64))
        self.assertEqual(
            shared_observation_profile("f" * 64, declared="full-faction-v1"),
            "full-faction-v1",
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            shared_observation_profile(
                "f" * 64, declared="full-faction-v1", requested="faction-unknown-v1"
            )

    def test_source_envelope_uses_public_relative_paths(self):
        files = source_manifest()["files"]
        self.assertIn("shared_player/compiler.py", files)
        self.assertIn("mod/ai/as3/GwaiSharedController.as", files)
        self.assertFalse(any(Path(name).is_absolute() for name in files))
