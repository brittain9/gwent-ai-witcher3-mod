"""Inference downloads carry parameters; game tables are reconstructed locally."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import policy_bundle, policy_format


class PolicyBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = {
            "schema_version": 1,
            "cards": [{"id": 9002, "effects": [4]}, {"id": 9001, "effects": []}],
            "decks": [],
            "runtime_mappings": {},
            "source": "local extraction",
        }
        self.catalog_path = self.root / "catalog.json"
        self.catalog_path.write_text(json.dumps(self.catalog))
        self.metadata = self.make_metadata()
        self.payload = struct.pack("<f", 0.125) * self.metadata["parameter_count"]
        sha = hashlib.sha256(self.payload).hexdigest()
        self.metadata.update(export_sha256=sha, weights_sha256=sha)
        self.export_dir = self.root / "export"
        self.export_dir.mkdir()
        self.write_export()

    def make_metadata(self, *, cards: int = 2, width: int = 2, blocks: int = 1) -> dict:
        observations = cards * 24 + 18 * 45 + 64
        shapes = policy_bundle.parameter_shapes(observations, cards, width, blocks)
        offsets = {}
        count = 0
        for name, shape in shapes.items():
            offsets[name] = count
            count += math.prod(shape)
        return {
            "schema": 1,
            "observation_schema_id": "witcher3-gwent-seat-view-v1",
            "observation_profile_id": policy_format.FULL_FACTION_PROFILE,
            "action_schema_id": "witcher3-gwent-factorized-action-v1",
            "checkpoint_sha256": "a" * 64,
            "update_count": 7,
            "export_sha256": "b" * 64,
            "catalog_sha256": policy_format.catalog_identity(self.catalog),
            "card_ids": [9001, 9002],
            "card_effects": [[], [4]],
            "run_id": "local-provenance-that-must-not-ship",
            "source_path": "/local-machine/checkpoint.pt",
            "observation_size": observations,
            "card_count": cards,
            "width": width,
            "blocks": blocks,
            "parameter_count": count,
            "weights_format": "ieee754-float32-little-endian",
            "weight_bytes": count * 4,
            "weights_sha256": "b" * 64,
            "weights_class_name": "GwaiNetWeightsData_" + "a" * 16,
            "offsets": offsets,
            "shapes": shapes,
        }

    def write_export(self) -> None:
        (self.export_dir / "metadata.json").write_text(json.dumps(self.metadata))
        (self.export_dir / "policy.f32.bin").write_bytes(self.payload)

    def create(self, name: str = "bundle", **kwargs) -> Path:
        output = self.root / name
        policy_bundle.create_bundle(
            {tier: self.export_dir for tier in policy_bundle.TIERS}, output, **kwargs
        )
        return output

    def mutate_manifest(self, bundle: Path, change) -> None:
        path = bundle / "manifest.json"
        manifest = json.loads(path.read_text())
        change(manifest)
        path.write_text(json.dumps(manifest))

    def replace_tier(
        self,
        bundle: Path,
        tier: str,
        *,
        cards: int = 2,
        width: int = 2,
        blocks: int = 1,
    ) -> dict:
        metadata = self.make_metadata(cards=cards, width=width, blocks=blocks)
        metadata["checkpoint_sha256"] = "b" * 64
        payload = struct.pack("<f", 0.125) * metadata["parameter_count"]
        sha = hashlib.sha256(payload).hexdigest()
        metadata.update(export_sha256=sha, weights_sha256=sha)
        relative = f"weights/{sha}.f32.bin"
        (bundle / relative).write_bytes(payload)
        entry = {key: metadata[key] for key in policy_bundle.METADATA_FIELDS}
        entry["weights_path"] = relative
        self.mutate_manifest(
            bundle, lambda manifest: manifest["tiers"].update({tier: entry})
        )
        return {key: metadata[key] for key in policy_bundle.METADATA_FIELDS}

    def test_download_has_only_sanitized_metadata_and_deduplicated_weights(
        self,
    ) -> None:
        bundle = self.create()
        manifest = json.loads((bundle / "manifest.json").read_text())
        encoded = json.dumps(manifest)
        for forbidden in ("card_ids", "card_effects", "run_id", "source_path", ".pt"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(len(list((bundle / "weights").iterdir())), 1)
        self.assertEqual(len(list(bundle.rglob("*.as"))), 0)
        self.assertEqual(set(manifest["tiers"]), set(policy_bundle.TIERS))
        self.assertEqual(policy_bundle.load_bundle(bundle).probes_path, None)

    def test_materialization_uses_sorted_local_game_tables_and_same_header(
        self,
    ) -> None:
        bundle = self.create()
        prepared = policy_bundle.materialize(
            bundle, self.catalog_path, self.root / "prepared"
        )
        easy = prepared["easy"]
        self.assertEqual(easy.metadata["card_ids"], [9001, 9002])
        self.assertEqual(easy.metadata["card_effects"], [[], [4]])
        self.assertEqual(easy.export_dir.name, "a" * 64)
        self.assertEqual(easy.weights_path.read_bytes(), self.payload)
        self.assertEqual(easy, prepared["hard"])
        original = self.root / "expected.as"
        policy_format.write_embed_header(
            original, self.payload, self.metadata, easy.class_name
        )
        self.assertEqual(easy.header_path.read_bytes(), original.read_bytes())
        self.assertEqual(json.loads(easy.metadata_path.read_text()), easy.metadata)

    def test_wrong_catalog_is_refused_before_creating_output(self) -> None:
        bundle = self.create()
        self.catalog["cards"][0]["effects"] = [5]
        self.catalog_path.write_text(json.dumps(self.catalog))
        target = self.root / "prepared"
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "catalog"):
            policy_bundle.materialize(bundle, self.catalog_path, target)
        self.assertFalse(target.exists())

    def test_tiers_with_same_catalog_hash_require_the_same_vocabulary_geometry(
        self,
    ) -> None:
        for tier in ("normal", "hard"):
            with self.subTest(tier=tier):
                bundle = self.create(f"different-{tier}-vocabulary")
                self.replace_tier(bundle, tier, cards=3)
                with self.assertRaisesRegex(
                    policy_bundle.PolicyBundleError, "vocabulary"
                ):
                    policy_bundle.load_bundle(bundle)

    def test_local_catalog_is_validated_for_each_tier_before_materialization(
        self,
    ) -> None:
        bundle = self.create()
        loaded = policy_bundle.load_bundle(bundle)
        different = self.make_metadata(cards=3)
        loaded.metadata["hard"] = {
            key: different[key] for key in policy_bundle.METADATA_FIELDS
        }
        target = self.root / "invalid-local-exports"
        with mock.patch.object(policy_bundle, "load_bundle", return_value=loaded):
            with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "vocabulary"):
                policy_bundle.materialize(bundle, self.catalog_path, target)
        self.assertFalse(target.exists())

    def test_tiers_can_use_different_widths_and_blocks_with_same_vocabulary(
        self,
    ) -> None:
        bundle = self.create()
        self.replace_tier(bundle, "normal", width=3, blocks=2)
        prepared = policy_bundle.materialize(
            bundle, self.catalog_path, self.root / "different-architecture"
        )
        self.assertEqual(prepared["normal"].metadata["width"], 3)
        self.assertEqual(prepared["normal"].metadata["blocks"], 2)
        self.assertEqual(prepared["normal"].metadata["card_ids"], [9001, 9002])

    def test_legacy_bundle_keeps_generic_profile_and_unknown_profile_is_refused(self):
        bundle = self.create()
        self.mutate_manifest(
            bundle,
            lambda manifest: [
                entry.pop("observation_profile_id")
                for entry in manifest["tiers"].values()
            ],
        )
        loaded = policy_bundle.load_bundle(bundle)
        self.assertTrue(
            all(
                item["observation_profile_id"] == policy_format.FULL_FACTION_PROFILE
                for item in loaded.metadata.values()
            )
        )
        self.mutate_manifest(
            bundle,
            lambda manifest: manifest["tiers"]["hard"].update(
                observation_profile_id="unrecognized-v1"
            ),
        )
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "profile"):
            policy_bundle.load_bundle(bundle)

    def test_explicit_audited_profile_migration_preserves_payload_and_source(self):
        self.metadata.pop("observation_profile_id")
        self.metadata["checkpoint_sha256"] = next(
            iter(policy_format.FACTION_UNKNOWN_CHECKPOINTS)
        )
        self.write_export()
        before = (self.export_dir / "metadata.json").read_bytes()
        destination = self.root / "migrated"
        policy_bundle.create_bundle(
            {tier: self.export_dir for tier in policy_bundle.TIERS},
            destination,
            observation_profile_id=policy_format.UNKNOWN_FACTION_PROFILE,
        )
        prepared = policy_bundle.materialize(
            destination, self.catalog_path, self.root / "migrated-exports"
        )
        for item in prepared.values():
            self.assertEqual(
                item.metadata["observation_profile_id"],
                policy_format.UNKNOWN_FACTION_PROFILE,
            )
            self.assertEqual(item.weights_path.read_bytes(), self.payload)
        self.assertEqual((self.export_dir / "metadata.json").read_bytes(), before)
        self.metadata["checkpoint_sha256"] = "0" * 64
        self.write_export()
        refused = self.root / "unaudited"
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "not audited"):
            policy_bundle.create_bundle(
                {tier: self.export_dir for tier in policy_bundle.TIERS},
                refused,
                observation_profile_id=policy_format.UNKNOWN_FACTION_PROFILE,
            )
        self.assertFalse(refused.exists())

    def test_same_checkpoint_cannot_name_different_profiles(self):
        bundle = self.create()
        self.mutate_manifest(
            bundle,
            lambda manifest: manifest["tiers"]["hard"].update(
                observation_profile_id=policy_format.UNKNOWN_FACTION_PROFILE
            ),
        )
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "inconsistent"):
            policy_bundle.load_bundle(bundle)

    def test_existing_output_is_preserved(self) -> None:
        bundle = self.create()
        marker = bundle / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.create()
        self.assertEqual(marker.read_text(), "keep")

    def test_corrupt_weights_and_unlisted_material_are_refused(self) -> None:
        bundle = self.create()
        loaded = policy_bundle.load_bundle(bundle)
        payload = loaded.weights_paths["easy"]
        original = payload.read_bytes()
        payload.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "hash"):
            policy_bundle.load_bundle(bundle)
        payload.write_bytes(original)
        (bundle / "generated-table.json").write_text("[]")
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "unlisted"):
            policy_bundle.load_bundle(bundle)

    def test_manifest_cannot_add_game_tables_or_external_paths(self) -> None:
        for field, value in [
            ("card_ids", [9001, 9002]),
            ("weights_path", "../outside.bin"),
        ]:
            with self.subTest(field=field):
                bundle = self.create(field)
                self.mutate_manifest(
                    bundle, lambda m: m["tiers"]["easy"].update({field: value})
                )
                with self.assertRaises(policy_bundle.PolicyBundleError):
                    policy_bundle.load_bundle(bundle)

    def test_duplicate_json_keys_are_refused(self) -> None:
        bundle = self.create()
        path = bundle / "manifest.json"
        content = path.read_text()
        path.write_text(
            content.replace('"format":', '"format":"duplicate","format":', 1)
        )
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "duplicate"):
            policy_bundle.load_bundle(bundle)

    def test_bad_tensor_layout_cannot_be_relabelled_as_valid_weights(self) -> None:
        cases = [
            lambda m: m["offsets"].update({"input.bias": 0}),
            lambda m: m["shapes"].update({"input.weight": [2, 1]}),
            lambda m: m.update({"width": True}),
            lambda m: m.update({"observation_size": 1}),
            lambda m: m.update({"blocks": 1000000}),
        ]
        original = copy.deepcopy(self.metadata)
        for index, mutate in enumerate(cases):
            with self.subTest(index=index):
                bad = copy.deepcopy(original)
                mutate(bad)
                self.metadata = bad
                self.write_export()
                with self.assertRaises(policy_bundle.PolicyBundleError):
                    self.create(f"bad-{index}")
                self.metadata = copy.deepcopy(original)

    def test_nonfinite_tensor_payload_is_refused_even_with_matching_hash(self) -> None:
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                self.payload = struct.pack("<f", value) + self.payload[4:]
                sha = hashlib.sha256(self.payload).hexdigest()
                self.metadata.update(export_sha256=sha, weights_sha256=sha)
                self.write_export()
                with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "finite"):
                    self.create("nonfinite")

    def test_symlinked_weights_are_refused(self) -> None:
        bundle = self.create()
        path = policy_bundle.load_bundle(bundle).weights_paths["easy"]
        outside = self.root / "outside.bin"
        path.rename(outside)
        try:
            path.symlink_to(outside)
        except OSError:
            self.skipTest("this platform does not permit creating symlinks")
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "symlink"):
            policy_bundle.load_bundle(bundle)

    def test_probe_artifact_is_hash_bound_and_preserved(self) -> None:
        probes = self.root / "probes.json"
        probes.write_text('{"format":"witcher3-policy-probes-v1","models":{}}')
        bundle = self.create(probes_path=probes)
        loaded = policy_bundle.load_bundle(bundle)
        self.assertEqual(loaded.probes_path.read_bytes(), probes.read_bytes())
        original = loaded.probes_path.read_bytes()
        loaded.probes_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        with self.assertRaisesRegex(policy_bundle.PolicyBundleError, "hash"):
            policy_bundle.load_bundle(bundle)

    def test_portable_module_does_not_import_numerical_packages(self) -> None:
        root = Path(__file__).resolve().parents[1]
        code = (
            "import sys; from scripts import policy_bundle; "
            "assert 'torch' not in sys.modules; assert 'numpy' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-S", "-c", code], cwd=root, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
