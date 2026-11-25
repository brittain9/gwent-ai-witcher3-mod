"""Focused schema, serving identity and rendered configuration regression checks."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shared_player.bundle import (
    SharedBundleError,
    serving_identity,
    validate_controller,
    validate_serving_identity,
)
from shared_player.compiler import CompilerError, verify_compiled_settings
from shared_player.sources import _configuration


def settings():
    return {
        "enabled": True,
        "mode": "policy-only",
        "terminal_pass_v1": True,
        "difficulties": {
            str(level): {
                "selector": "joint-argmax" if level == 3 else "group-first",
                "temperature": temperature,
                "planning_worlds": 0,
                "rollout_primary_turns": 0,
                "deadline_ms": 0,
            }
            for level, temperature in ((1, 1.0), (2, 0.25), (3, 0.0))
        },
    }


class IntegrationTests(unittest.TestCase):
    def test_schema_rejects_missing_unknown_and_stochastic_joint_modes(self):
        valid = settings()
        self.assertEqual(validate_controller(valid), valid)
        for edit in ("missing", "unknown", "temperature"):
            invalid = copy.deepcopy(valid)
            level = invalid["difficulties"]["3"]
            if edit == "missing":
                del level["selector"]
            elif edit == "unknown":
                level["selector"] = "unknown"
            else:
                level["temperature"] = 1
            with self.subTest(edit=edit), self.assertRaises(SharedBundleError):
                validate_controller(invalid)

    def test_default_builder_keeps_original_difficulty_and_group_first(self):
        from shared_player.build import build

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "model.pt"
            checkpoint.write_bytes(b"synthetic")

            def prepare(**kwargs):
                output = kwargs["output"]
                output.mkdir(parents=True)
                manifest = output / "build.json"
                manifest.write_text("{}")
                return manifest

            metadata = {
                "checkpoint_sha256": "a" * 64,
                "weights_sha256": "b" * 64,
                "observation_profile_id": "full-faction-v1",
            }
            with (
                patch("scripts.export_policy_as3.export", return_value=metadata),
                patch("scripts.policy_probes.create_probes"),
                patch("shared_player.build.prepare", side_effect=prepare),
                patch(
                    "shared_player.build.build_player", return_value={"status": "ok"}
                ),
            ):
                build(
                    checkpoint=checkpoint,
                    catalog=root / "catalog.json",
                    output=root / "build",
                )
            controller = json.loads((root / "build-inputs/controller.json").read_text())
            self.assertTrue(controller["enabled"])
            self.assertTrue(controller["terminal_pass_v1"])
            self.assertEqual(controller["mode"], "policy-only")
            self.assertEqual(
                [
                    (
                        controller["difficulties"][str(level)]["selector"],
                        controller["difficulties"][str(level)]["temperature"],
                    )
                    for level in (1, 2, 3)
                ],
                [("group-first", 1.0), ("group-first", 0.25), ("group-first", 0.0)],
            )

    def test_config_identity_and_rendered_settings_reject_changes(self):
        controller = settings()
        identity = serving_identity(controller)
        validate_serving_identity(identity, controller)
        changed = copy.deepcopy(controller)
        changed["difficulties"]["3"]["selector"] = "group-first"
        with self.assertRaises(SharedBundleError):
            validate_serving_identity(identity, changed)
        metadata = {
            name: "x"
            for name in (
                "data_kind",
                "controller_contract",
                "counter_contract",
                "selection_rule",
                "information_protocol",
                "observation_profile_id",
                "controller_source_sha256",
                "training_manifest_sha256",
            )
        }
        metadata["serving"] = identity
        rendered = _configuration(controller, metadata, "Weights")
        verify_compiled_settings(rendered, controller)
        with self.assertRaises(CompilerError):
            verify_compiled_settings(rendered, changed)
        for field, value in identity.items():
            self.assertIn("SERVING_" + field.upper(), rendered)
            self.assertIn(value, rendered)


if __name__ == "__main__":
    unittest.main()
