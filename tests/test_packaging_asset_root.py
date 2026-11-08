"""Exercise the actual assembler with compiled derived settings, not raw defaults."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts import install_mod
from shared_player.packaging import (
    ASSET_PATHS,
    DEBUG_SINK,
    REFERENCE,
    assemble_verified,
    package_assets,
)


class AssetRootBoundaryTests(unittest.TestCase):
    def fixture(self, root, variant="diagnostic"):
        assets = package_assets(variant)
        build = root / "build"
        build.mkdir()
        payload = b"fictional compiled REDSWF payload"
        (build / "gwint_game.redswf").write_bytes(payload)
        pins = json.loads((REFERENCE / "mod/game-pins.json").read_text())
        vanilla = next(
            item["sha256"]
            for item in pins["bundle_members"]
            if item.get("role") == "replaced"
        )
        report = {
            "format": "witcher3-gwent-mod-build-v4",
            "variant": variant,
            "status": "offline_compiled_and_export_verified",
            "one_shared_model": True,
            "compiled_helpers": {"model_count": 1},
            "play_trace": {
                "schema": "w3gwent-playtrace-v1",
                "compiled_verified": True,
                "public_only": True,
                "helper_sha256": "a" * 64,
            },
            "inputs_sha256": {
                **{
                    name: hashlib.sha256(body).hexdigest()
                    for name, body in assets.items()
                },
                "gwint_game.redswf": vanilla,
            },
            "outputs_sha256": {
                "gwint_game.redswf": hashlib.sha256(payload).hexdigest()
            },
        }
        (build / "manifest.json").write_text(json.dumps(report))
        staged = root / "explicit-assets"
        paths = {**ASSET_PATHS, "gwai_policy_diagnostic.ws": DEBUG_SINK}
        for name, body in assets.items():
            target = staged / paths[name]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        return build, assets, staged

    def test_default_raw_checkout_cannot_package_derived_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build, assets, _ = self.fixture(root)
            self.assertNotEqual(
                assets["gwai_settings.ws"],
                (REFERENCE / ASSET_PATHS["gwai_settings.ws"]).read_bytes(),
            )
            with self.assertRaisesRegex(
                install_mod.ModInstallError, "build used gwai_settings"
            ):
                install_mod.assemble_package(
                    "diagnostic",
                    build_dir=build,
                    package_dir=root / "wrong",
                    pins_path=REFERENCE / "mod/game-pins.json",
                )
            self.assertFalse((root / "wrong").exists())

    def test_explicit_exact_derived_assets_pass_real_bundle_and_package_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build, assets, staged = self.fixture(root)
            package = install_mod.assemble_package(
                "diagnostic",
                build_dir=build,
                package_dir=root / "package",
                pins_path=REFERENCE / "mod/game-pins.json",
                asset_root=staged,
            )
            manifest, _ = install_mod._read_package(package)
            self.assertEqual(manifest["variant"], "diagnostic")
            # Read the declared mod name rather than assuming the diagnostic edition's folder.
            scripts = package / "Mods" / manifest["mod_name"] / "content/scripts/local"
            self.assertEqual(
                (scripts / "gwai_policy_diagnostic.ws").read_bytes(),
                assets["gwai_policy_diagnostic.ws"],
            )
            self.assertIn("rule", (scripts / "gwai_settings.ws").read_text())
            self.assertEqual(
                (package / "build-manifest.json").read_bytes(),
                (build / "manifest.json").read_bytes(),
            )
            (staged / ASSET_PATHS["gwai_settings.ws"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(
                install_mod.ModInstallError, "build used gwai_settings"
            ):
                install_mod.assemble_package(
                    "diagnostic",
                    build_dir=build,
                    package_dir=root / "tampered",
                    pins_path=REFERENCE / "mod/game-pins.json",
                    asset_root=staged,
                )
            self.assertFalse((root / "tampered").exists())

    def test_verified_handoff_stages_exact_assets_and_binds_assembly_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build, assets, _ = self.fixture(root)
            before = (build / "manifest.json").read_bytes()
            receipt = assemble_verified(
                build, root / "package", receipt_path=root / "proof.json"
            )
            self.assertEqual((build / "manifest.json").read_bytes(), before)
            self.assertEqual(
                receipt["asset_sha256"],
                {
                    name: hashlib.sha256(body).hexdigest()
                    for name, body in assets.items()
                },
            )
            self.assertEqual(
                receipt["native_asset_root"],
                str((root / "package-native-assets").resolve()),
            )
            self.assertEqual(
                receipt["assembler_source_sha256"],
                hashlib.sha256(Path(install_mod.__file__).read_bytes()).hexdigest(),
            )
            self.assertEqual(
                receipt["packager_source_sha256"],
                hashlib.sha256(
                    Path(
                        __import__(
                            "shared_player.packaging", fromlist=["__file__"]
                        ).__file__
                    ).read_bytes()
                ).hexdigest(),
            )
            self.assertFalse(receipt["native_verified"])


if __name__ == "__main__":
    unittest.main()
