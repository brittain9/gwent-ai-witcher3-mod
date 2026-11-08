"""Package proof closure and existing installer payload identity boundaries."""

import copy
import hashlib
import unittest
from unittest.mock import patch


def example():
    assets = {
        "gwai_settings.ws": b"fictional settings script",
        "gwai_playtrace.ws": b"fictional public trace sink",
        "modW3GwentAI.xml": (
            b"<UserConfig><Group><VisibleVars>"
            b'<Var id="GwaiEnabled"/><Var id="GwaiDevOpponent"/>'
            b'<Var id="GwaiDevStart"/><Var id="GwaiDevReport"/>'
            b'<Var id="GwaiSignal"/><Var id="GwaiTrace"/>'
            b"</VisibleVars></Group></UserConfig>"
        ),
    }
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in assets.items()}
    report = {
        "format": "witcher3-gwent-mod-build-v4",
        "variant": "ai",
        "status": "offline_compiled_and_export_verified",
        "one_shared_model": True,
        "compiled_helpers": {"model_count": 1},
        "play_trace": {
            "schema": "w3gwent-playtrace-v1",
            "compiled_verified": True,
            "public_only": True,
            "helper_sha256": "a" * 64,
        },
        "inputs_sha256": hashes,
    }
    return report, assets


class SharedPackagingTests(unittest.TestCase):
    def test_diagnostic_reference_does_not_mean_observer_class_is_installed(self):
        from scripts import mod_trace
        from shared_player.packaging import patch_public_trace

        ai = "package { internal class Diagnostic { function record() { GwaiPlayTrace.encodeDiagnostic({}); } } }"
        patched = {"AIPlayerController": ai}
        extra = {name: "" for name in mod_trace.EXTRA_CLASSES}
        helper = "internal class GwaiPlayTrace {}"
        # Native hook placement stays owned by the already tested existing module.
        with patch(
            "scripts.mod_trace.patch_sources", side_effect=lambda value: dict(value)
        ) as native_hooks:
            result = patch_public_trace(patched, extra, helper)
        native_hooks.assert_called_once()
        self.assertIn("GwaiPlayTrace.encodeDiagnostic", result["AIPlayerController"])
        self.assertEqual(result["AIPlayerController"].count(helper), 1)
        self.assertEqual(patched["AIPlayerController"], ai)

    def test_duplicate_observer_declaration_is_refused_before_native_hook_patching(
        self,
    ):
        from scripts import mod_trace
        from shared_player.packaging import patch_public_trace

        extra = {name: "" for name in mod_trace.EXTRA_CLASSES}
        helper = "internal class GwaiPlayTrace {}"
        with patch("scripts.mod_trace.patch_sources") as native_hooks:
            with self.assertRaisesRegex(ValueError, "already installed"):
                patch_public_trace(
                    {"AIPlayerController": "package {" + helper + "}"}, extra, helper
                )
            with self.assertRaisesRegex(ValueError, "inventory"):
                patch_public_trace(
                    {"AIPlayerController": "package {}"}, extra, helper + helper
                )
        native_hooks.assert_not_called()

    def test_package_closes_over_exact_existing_menu_and_scripts(self):
        from shared_player.packaging import verify_package_ready

        report, assets = example()
        proof = verify_package_ready(report, assets)
        self.assertTrue(proof["one_shared_model"])
        self.assertEqual(proof["payload_script_count"], 2)
        self.assertEqual(proof["menu_control_source"], "existing-six-control-menu")
        altered = {
            **assets,
            "gwai_settings.ws": assets["gwai_settings.ws"] + b"changed",
        }
        with self.assertRaisesRegex(ValueError, "hash|identity"):
            verify_package_ready(report, altered)
        expanded = dict(assets)
        expanded["modW3GwentAI.xml"] = assets["modW3GwentAI.xml"].replace(
            b"</VisibleVars>", b'<Var id="UnmeasuredThinkingTime"/></VisibleVars>'
        )
        matching_hash = copy.deepcopy(report)
        matching_hash["inputs_sha256"]["modW3GwentAI.xml"] = hashlib.sha256(
            expanded["modW3GwentAI.xml"]
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "six-control"):
            verify_package_ready(matching_hash, expanded)

    def test_offline_compile_without_native_trace_proof_is_not_packagable(self):
        from shared_player.packaging import verify_package_ready

        report, assets = example()
        for field, value in (
            ("play_trace", None),
            ("one_shared_model", False),
            ("compiled_helpers", {"model_count": 3}),
        ):
            changed = {**report, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_package_ready(changed, assets)

    def test_diagnostic_sink_cannot_enter_release_or_hide_missing_debug_sink(self):
        from shared_player.packaging import verify_package_ready

        report, assets = example()
        debug_assets = {**assets, "gwai_policy_diagnostic.ws": b"fictional debug sink"}
        with self.assertRaisesRegex(ValueError, "inventory|variant|diagnostic"):
            verify_package_ready(report, debug_assets)
        debug_report = copy.deepcopy(report)
        debug_report["variant"] = "diagnostic"
        with self.assertRaisesRegex(ValueError, "inventory|diagnostic"):
            verify_package_ready(debug_report, assets)
        debug_report["inputs_sha256"]["gwai_policy_diagnostic.ws"] = hashlib.sha256(
            debug_assets["gwai_policy_diagnostic.ws"]
        ).hexdigest()
        self.assertEqual(
            verify_package_ready(debug_report, debug_assets)["payload_script_count"], 3
        )


if __name__ == "__main__":
    unittest.main()
