"""Counter, native timing and owned action RNG compose without search/history."""

import json
import tempfile
import unittest
from pathlib import Path

from test_owned_action_host import sources as authored_owned_sources
from test_shared_player_bundle import (
    policy_only_controller,
    synthetic_catalog,
    synthetic_export,
)

from shared_player.owned_actions import verify_owned_actions


class PolicyRuntimeCompositionTests(unittest.TestCase):
    def test_owned_rng_wiring_is_closed_without_search_dependencies(self):
        from shared_player.owned_actions import patch_owned_actions

        source = patch_owned_actions(authored_owned_sources())
        self.assertFalse(verify_owned_actions(source)["runtime_execution_verified"])
        self.assertNotIn("GwaiWorldSearch", "\n".join(source.values()))

    def test_timing_helper_keeps_owned_uniforms_and_separates_weight_forward(self):
        from shared_player.bundle import create_bundle, materialize
        from shared_player.sources import build_sources

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = synthetic_export(root / "export")
            create_bundle(export, root / "bundle", controller=policy_only_controller())
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(synthetic_catalog()))
            prepared = materialize(root / "bundle", catalog, root / "prepared")
            release = build_sources(prepared)
            timing = build_sources(
                prepared, edition="diagnostic", diagnostic_mode="timing-only"
            )
            self.assertIn("this._actionUniforms", timing["GwaiSharedController"])
            self.assertEqual(
                timing["GwaiSharedController"].count("GwaiNetPolicy.evaluate("), 1
            )
            self.assertEqual(
                timing["GwaiSharedController"].count("GwaiNetWeights.load(data)"), 1
            )
            self.assertNotIn("timingMark(", release["GwaiSharedController"])
            self.assertFalse(
                any(
                    name.startswith("GwaiSim")
                    or name
                    in {"GwaiKnownState", "GwaiPlayerHistory", "GwaiWorldSearch"}
                    for name in release
                )
            )

    def test_incomplete_native_timing_inputs_are_refused(self):
        from shared_player.host import HostIntegrationError
        from shared_player.timing_hooks import patch_timing_runtime

        with self.assertRaises((HostIntegrationError, KeyError)):
            patch_timing_runtime(
                {"AIPlayerController": "internal class AIPlayerController {}"}
            )
