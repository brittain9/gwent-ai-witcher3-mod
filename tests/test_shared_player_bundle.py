"""One shared export and controller settings replace tier-specific models."""

from __future__ import annotations

import hashlib
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path


def synthetic_catalog() -> dict:
    return {
        "schema_version": 1,
        "cards": [{"id": 9002, "effects": [4]}, {"id": 9001, "effects": []}],
        "decks": [],
        "runtime_mappings": {},
    }


def synthetic_export(root: Path) -> Path:
    """Independent, fictional two-card fixture in the existing tensor format."""
    width = 2
    cards = 2
    observations = 24 * cards + 18 * 45 + 64
    shapes = {
        "input.weight": [width, observations],
        "input.bias": [width],
        "input_norm.weight": [width],
        "input_norm.bias": [width],
        "blocks.0.norm.weight": [width],
        "blocks.0.norm.bias": [width],
        "blocks.0.first.weight": [width, width],
        "blocks.0.first.bias": [width],
        "blocks.0.second.weight": [width, width],
        "blocks.0.second.bias": [width],
        "kind_embedding.weight": [6, 24],
        "card_embedding.weight": [cards + 1, 48],
        "row_embedding.weight": [4, 8],
        "action_projection.weight": [width, 176],
        "action_projection.bias": [width],
        "group_projection.weight": [width, 72],
        "group_projection.bias": [width],
        "local_actor.0.weight": [128, 2 * width],
        "local_actor.0.bias": [128],
        "local_actor.2.weight": [1, 128],
        "local_actor.2.bias": [1],
        "group_actor.0.weight": [128, 2 * width],
        "group_actor.0.bias": [128],
        "group_actor.2.weight": [1, 128],
        "group_actor.2.bias": [1],
        "value_head.0.weight": [128, width],
        "value_head.0.bias": [128],
        "value_head.2.weight": [3, 128],
        "value_head.2.bias": [3],
    }
    offsets = {}
    count = 0
    for name, shape in shapes.items():
        offsets[name] = count
        count += math.prod(shape)
    payload = struct.pack("<f", 0.125) * count
    digest = hashlib.sha256(payload).hexdigest()
    metadata = {
        "schema": 1,
        # A fictional schema example, never an admitted gameplay artifact.
        "data_kind": "gameplay",
        "observation_schema_id": "witcher3-gwent-seat-view-v1",
        "observation_profile_id": "full-faction-v1",
        "action_schema_id": "witcher3-gwent-factorized-action-v1",
        "controller_contract": "deployed-controller-v1",
        "counter_contract": "completed-primary-turns-v1",
        "selection_rule": "served-group-first-v1",
        "information_protocol": "seat-view-v1",
        "controller_source_sha256": "c" * 64,
        "training_manifest_sha256": "d" * 64,
        "checkpoint_sha256": "a" * 64,
        "update_count": 7,
        "export_sha256": digest,
        "catalog_sha256": hashlib.sha256(
            json.dumps(
                synthetic_catalog(), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "card_ids": [9001, 9002],
        "card_effects": [[], [4]],
        "run_id": "private-training-run",
        "source_path": "/private/checkpoint.pt",
        "observation_size": observations,
        "card_count": cards,
        "width": width,
        "blocks": 1,
        "parameter_count": count,
        "weights_format": "ieee754-float32-little-endian",
        "weight_bytes": len(payload),
        "weights_sha256": digest,
        "offsets": offsets,
        "shapes": shapes,
    }
    root.mkdir()
    (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (root / "policy.f32.bin").write_bytes(payload)
    return root


def policy_only_controller() -> dict:
    return {
        "enabled": False,
        "mode": "policy-only",
        "difficulties": {
            str(level): {
                "temperature": 0.0,
                "planning_worlds": 0,
                "rollout_primary_turns": 0,
                "deadline_ms": 0,
            }
            for level in (1, 2, 3)
        },
    }


class SharedPlayerBundleTests(unittest.TestCase):
    def test_one_export_binds_one_model_and_only_controller_difficulty(self):
        from shared_player.bundle import create_bundle, load_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            output = root / "bundle"
            created = create_bundle(
                exported, output, controller=policy_only_controller()
            )
            loaded = load_bundle(output)

            self.assertEqual(created, loaded.manifest)
            self.assertEqual(created["format"], "witcher3-shared-policy-bundle-v1")
            self.assertEqual(created["model"]["checkpoint_sha256"], "a" * 64)
            self.assertNotIn("tiers", created)
            self.assertNotIn("models", created)
            self.assertEqual(created["controller"], policy_only_controller())
            self.assertEqual(len(list((output / "weights").iterdir())), 1)
            self.assertEqual(
                loaded.weights_path.read_bytes(),
                (exported / "policy.f32.bin").read_bytes(),
            )
            serialized = json.dumps(created)
            for forbidden in (
                "card_ids",
                "card_effects",
                "run_id",
                "source_path",
                "private-training-run",
                "checkpoint.pt",
            ):
                self.assertNotIn(forbidden, serialized)
            self.assertFalse(created["controller"]["enabled"])
            self.assertEqual(
                set(created["controller"]["difficulties"]), {"1", "2", "3"}
            )

    def test_policy_only_candidate_refuses_search_configuration(self):
        from shared_player.bundle import SharedBundleError, create_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            controller = policy_only_controller()
            controller["difficulties"]["3"]["planning_worlds"] = 8
            output = root / "bundle"
            with self.assertRaisesRegex(SharedBundleError, "policy-only"):
                create_bundle(exported, output, controller=controller)
            self.assertFalse(output.exists())

    def test_missing_counter_contract_is_refused_before_output(self):
        from shared_player.bundle import SharedBundleError, create_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            metadata_path = exported / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            del metadata["counter_contract"]
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            output = root / "bundle"
            with self.assertRaisesRegex(SharedBundleError, "counter_contract"):
                create_bundle(exported, output, controller=policy_only_controller())
            self.assertFalse(output.exists())

    def test_one_materialized_header_preserves_payload_and_local_vocabulary(self):
        from shared_player.bundle import create_bundle, materialize

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            create_bundle(
                exported, root / "bundle", controller=policy_only_controller()
            )
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps(synthetic_catalog()), encoding="utf-8")
            prepared = materialize(root / "bundle", catalog_path, root / "prepared")
            self.assertEqual(prepared.metadata["card_ids"], [9001, 9002])
            self.assertEqual(prepared.metadata["card_effects"], [[], [4]])
            self.assertEqual(
                prepared.weights_path.read_bytes(),
                (exported / "policy.f32.bin").read_bytes(),
            )
            self.assertEqual(len(list(prepared.export_dir.glob("*.as"))), 1)
            self.assertNotIn("Tier", prepared.header_path.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(prepared.controller_path.read_text(encoding="utf-8")),
                policy_only_controller(),
            )
            self.assertEqual(
                json.loads(prepared.metadata_path.read_text(encoding="utf-8")),
                prepared.metadata,
            )

    def test_wrong_local_catalog_refuses_materialization_before_output(self):
        from shared_player.bundle import SharedBundleError, create_bundle, materialize

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            create_bundle(
                exported, root / "bundle", controller=policy_only_controller()
            )
            catalog = synthetic_catalog()
            catalog["cards"][0]["effects"] = [5]
            path = root / "catalog.json"
            path.write_text(json.dumps(catalog), encoding="utf-8")
            target = root / "prepared"
            with self.assertRaisesRegex(SharedBundleError, "catalog"):
                materialize(root / "bundle", path, target)
            self.assertFalse(target.exists())

    def test_changed_weight_payload_is_rejected(self):
        from shared_player.bundle import SharedBundleError, create_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            path = exported / "policy.f32.bin"
            data = bytearray(path.read_bytes())
            data[0] ^= 1
            path.write_bytes(data)
            with self.assertRaisesRegex(SharedBundleError, "hash"):
                create_bundle(
                    exported, root / "bundle", controller=policy_only_controller()
                )
            self.assertFalse((root / "bundle").exists())

    def test_hash_consistent_nonfinite_weights_are_still_rejected(self):
        from shared_player.bundle import SharedBundleError, create_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            path = exported / "policy.f32.bin"
            data = struct.pack("<f", float("nan")) + path.read_bytes()[4:]
            path.write_bytes(data)
            metadata_path = exported / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            digest = hashlib.sha256(data).hexdigest()
            metadata.update(weights_sha256=digest, export_sha256=digest)
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(SharedBundleError, "non-finite"):
                create_bundle(
                    exported, root / "bundle", controller=policy_only_controller()
                )
            self.assertFalse((root / "bundle").exists())

    def test_wrong_value_head_layout_is_refused(self):
        from shared_player.bundle import SharedBundleError, create_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            path = exported / "metadata.json"
            metadata = json.loads(path.read_text(encoding="utf-8"))
            metadata["shapes"]["value_head.2.weight"] = [2, 128]
            path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(SharedBundleError, "tensor shape"):
                create_bundle(
                    exported, root / "bundle", controller=policy_only_controller()
                )
            self.assertFalse((root / "bundle").exists())

    def test_absent_or_wrong_explicit_profile_is_not_inferred(self):
        from shared_player.bundle import SharedBundleError, create_bundle

        for value in (None, "legacy", [], True):
            with (
                self.subTest(profile=value),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                exported = synthetic_export(root / "export")
                path = exported / "metadata.json"
                metadata = json.loads(path.read_text(encoding="utf-8"))
                if value is None:
                    del metadata["observation_profile_id"]
                else:
                    metadata["observation_profile_id"] = value
                path.write_text(json.dumps(metadata), encoding="utf-8")
                with self.assertRaisesRegex(
                    SharedBundleError, "observation_profile_id"
                ):
                    create_bundle(
                        exported, root / "bundle", controller=policy_only_controller()
                    )
                self.assertFalse((root / "bundle").exists())

    def test_bundle_with_second_model_or_unlisted_file_is_refused(self):
        from shared_player.bundle import SharedBundleError, create_bundle, load_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            output = root / "bundle"
            create_bundle(exported, output, controller=policy_only_controller())
            extra = output / "weights" / "second.f32.bin"
            extra.write_bytes((exported / "policy.f32.bin").read_bytes())
            with self.assertRaisesRegex(SharedBundleError, "unlisted"):
                load_bundle(output)

    def test_tier_map_is_rejected_instead_of_reinterpreted(self):
        from shared_player.bundle import SharedBundleError, create_bundle, load_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            output = root / "bundle"
            create_bundle(exported, output, controller=policy_only_controller())
            path = output / "manifest.json"
            metadata = json.loads(path.read_text(encoding="utf-8"))
            metadata["tiers"] = {"easy": metadata["model"]}
            path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(SharedBundleError, "bundle fields"):
                load_bundle(output)

    def test_existing_output_is_preserved(self):
        from shared_player.bundle import create_bundle

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            output = root / "bundle"
            output.mkdir()
            marker = output / "user-owned"
            marker.write_bytes(b"preserve")
            with self.assertRaises(FileExistsError):
                create_bundle(exported, output, controller=policy_only_controller())
            self.assertEqual(list(output.iterdir()), [marker])
            self.assertEqual(marker.read_bytes(), b"preserve")

    def test_generated_release_and_diagnostic_share_model_but_separate_capture(self):
        from shared_player.bundle import create_bundle, materialize
        from shared_player.sources import build_sources, source_identity

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            create_bundle(
                exported, root / "bundle", controller=policy_only_controller()
            )
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(synthetic_catalog()), encoding="utf-8")
            prepared = materialize(root / "bundle", catalog, root / "prepared")
            release = build_sources(prepared)
            diagnostic = build_sources(prepared, edition="diagnostic")
            self.assertEqual(set(diagnostic) - set(release), {"GwaiSharedDiagnostic"})
            self.assertEqual(
                len(
                    [name for name in release if name.startswith("GwaiNetWeightsData_")]
                ),
                1,
            )
            self.assertEqual(
                release[prepared.class_name], diagnostic[prepared.class_name]
            )
            self.assertEqual(release["GwaiNetPolicy"], diagnostic["GwaiNetPolicy"])
            self.assertIn("return false", release["GwaiSharedConfig"])
            self.assertNotIn("GwaiSharedDiagnostic", "\n".join(release.values()))
            self.assertNotIn("TierConfig", "\n".join(diagnostic.values()))
            self.assertIn("deployed-controller-v1", release["GwaiSharedConfig"])
            self.assertIn("completed-primary-turns-v1", release["GwaiSharedConfig"])
            self.assertNotEqual(source_identity(release), source_identity(diagnostic))
            self.assertFalse(source_identity(release)["native_verified"])
            self.assertFalse(source_identity(release)["strength_evidence"])

    def test_changed_prepared_header_is_refused_before_source_generation(self):
        from shared_player.bundle import SharedBundleError, create_bundle, materialize
        from shared_player.sources import build_sources

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exported = synthetic_export(root / "export")
            create_bundle(
                exported, root / "bundle", controller=policy_only_controller()
            )
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(synthetic_catalog()), encoding="utf-8")
            prepared = materialize(root / "bundle", catalog, root / "prepared")
            prepared.header_path.write_text("tampered source", encoding="utf-8")
            with self.assertRaisesRegex(SharedBundleError, "header changed"):
                build_sources(prepared)

    def test_synthetic_absent_and_unknown_data_kinds_cannot_become_player_bundles(self):
        from shared_player.bundle import SharedBundleError, create_bundle

        for kind in (None, "synthetic", "unknown", True):
            with (
                self.subTest(data_kind=kind),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                exported = synthetic_export(root / "export")
                path = exported / "metadata.json"
                metadata = json.loads(path.read_text(encoding="utf-8"))
                if kind is None:
                    del metadata["data_kind"]
                else:
                    metadata["data_kind"] = kind
                path.write_text(json.dumps(metadata), encoding="utf-8")
                output = root / "bundle"
                with self.assertRaisesRegex(SharedBundleError, "data_kind"):
                    create_bundle(exported, output, controller=policy_only_controller())
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
