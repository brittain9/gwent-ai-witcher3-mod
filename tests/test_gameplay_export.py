"""Genuine learned-checkpoint provenance; fictional schema fixtures, no models."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def fictional_identity():
    return {
        "data_kind": "gameplay",
        "target_source": "onpolicy",
        "behavior_selection_rule": "factorized-categorical-v1",
        "training_manifest_sha256": "a" * 64,
        "controller_source_sha256": "b" * 64,
        "controller_source_scope": "native-controller-source-manifest-v1",
        "tensor_sha256": "c" * 64,
        "catalog_sha256": "d" * 64,
        "card_ids": [1, 2],
        "run_id": "fictional-schema-example",
        "update_id": 1,
        "updates": 4,
        "updates_scope": "completed-optimizer-steps-in-this-run-v1",
        "information_protocol": "seat-view-v1",
        "initialization": {
            "kind": "parent",
            "checkpoint_sha256": "e" * 64,
            "tensor_sha256": "f" * 64,
        },
    }


def fictional_config():
    return {
        "observation_schema_id": "witcher3-gwent-seat-view-v1",
        "action_schema_id": "witcher3-gwent-factorized-action-v1",
        "observation_profile_id": "faction-unknown-v1",
        "controller_contract": "deployed-controller-v1",
        "counter_contract": "completed-primary-turns-v1",
        "selection_rule": "served-group-first-v1",
        "card_count": 2,
        "observation_size": 100,
        "width": 8,
        "blocks": 1,
    }


def producer_fixture(root):
    """Fictional file identities only; no game or valid checkpoint is created."""

    def write(name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) if not isinstance(value, str) else value)
        return {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    entries = []
    for name in (
        "pyproject.toml",
        "uv.lock",
        "shared_learning/onpolicy_run.py",
        "shared_learning/onpolicy_training.py",
    ):
        item = write("runtime/" + name, "fictional source placeholder\n")
        entries.append({"relative": name, "sha256": item["sha256"]})
    runtime_hash = hashlib.sha256()
    for item in sorted(entries, key=lambda row: row["relative"]):
        runtime_hash.update(
            item["relative"].encode() + b"\0" + item["sha256"].encode() + b"\n"
        )
    source = write("runtime/source-manifest.json", {"files": entries})
    files = {
        name: write(name, "fictional " + name)
        for name in (
            "engine",
            "catalog",
            "initial_checkpoint",
            "train_cohort",
            "validation_cohort",
            "controller_source",
        )
    }
    files["engine"]["controller_source_sha256"] = files["controller_source"]["sha256"]
    manifest = {
        "purpose": "native-primary-onpolicy-pilot",
        "run_id": "fictional-schema-fixture",
        **files,
        "runtime_source_sha256": runtime_hash.hexdigest(),
        "observation_profile_id": "faction-unknown-v1",
    }
    manifest_spec = write("manifest.json", manifest)
    episodes = []
    for index in range(2):
        journal = write(f"journal-{index}.jsonl", "{}\n")
        episodes.append(
            {
                "episode_id": f"fictional:{index}",
                "journal_path": journal["path"],
                "journal_sha256": journal["sha256"],
                "journal_bytes": 3,
            }
        )
    corpus = {
        "training_manifest_sha256": manifest_spec["sha256"],
        "batch_index": 0,
        "update_id": 0,
        "complete_training_games": 2,
        "failed_training_games": 0,
        "rows": 10,
        "policy_revision": "c" * 64,
        "episodes": episodes,
    }
    corpus_spec = write("corpus.json", corpus)
    checkpoint = write(
        "shared.pt", "fictional invalid checkpoint; never a player artifact"
    )
    result = {
        "status": "pass",
        "data_kind": "gameplay",
        "purpose": manifest["purpose"],
        "run_id": manifest["run_id"],
        "training_manifest_sha256": manifest_spec["sha256"],
        "reload_tensor_identity_verified": True,
        "frozen_parent_identity_verified": True,
        "games_planned": 4,
        "games_completed": 4,
        "games_failed": 0,
        "games_incomplete": 0,
        "games_unstarted": 0,
        "stage_counts": {
            stage: {"planned": 2, "completed": 2, "failed": 0, "unstarted": 0}
            for stage in ("train", "validation")
        },
        "checkpoint": {**checkpoint, "tensor_sha256": "c" * 64},
        "completed_optimizer_updates": 4,
        "optimizer_results": [
            {
                "finite_update": True,
                "optimizer_steps": 4,
                "tensor_sha256_after": "c" * 64,
            }
        ],
        "training_rows": 10,
        "completed_training_batches": 1,
    }
    result_spec = write("result.json", result)
    plan = {
        "format": "witcher3-genuine-shared-export-inputs-v1",
        "inputs": {
            "checkpoint": checkpoint,
            "training_manifest": manifest_spec,
            "pipeline_result": result_spec,
            "training_source_manifest": source,
        },
        "corpus_receipts": [corpus_spec],
    }
    plan_spec = write("plan.json", plan)
    return Path(plan_spec["path"]), plan, result, episodes


class GameplayExportIdentityTests(unittest.TestCase):
    def test_genuine_identity_preserves_completed_steps_and_native_scope_without_defaults(
        self,
    ):
        from shared_player.gameplay_export import admit_gameplay_identity

        identity, config = fictional_identity(), fictional_config()
        admitted = admit_gameplay_identity(
            identity,
            config,
            training_manifest_sha256="a" * 64,
            controller_source_sha256="b" * 64,
            completed_updates=4,
        )
        self.assertEqual(admitted["data_kind"], "gameplay")
        self.assertEqual(admitted["update_count"], 4)
        self.assertEqual(admitted["behavior_generation"], 1)
        self.assertEqual(
            admitted["controller_source_scope"], "native-controller-source-manifest-v1"
        )
        self.assertEqual(admitted["tensor_sha256"], "c" * 64)
        self.assertEqual(admitted["initialization"], identity["initialization"])
        self.assertEqual(admitted["observation_profile_id"], "faction-unknown-v1")
        self.assertNotIn("historical_training", admitted)
        self.assertEqual(identity, fictional_identity())
        for field, wrong in (
            ("data_kind", "synthetic"),
            ("data_kind", None),
            ("target_source", "unknown"),
            ("updates", 1),
            ("controller_source_scope", "historical-training-code-tree-v1"),
            ("information_protocol", "oracle"),
        ):
            altered = copy.deepcopy(identity)
            if wrong is None:
                del altered[field]
            else:
                altered[field] = wrong
            with self.subTest(field=field, wrong=wrong), self.assertRaises(ValueError):
                admit_gameplay_identity(
                    altered,
                    config,
                    training_manifest_sha256="a" * 64,
                    controller_source_sha256="b" * 64,
                    completed_updates=4,
                )

    def test_complete_producer_file_preflight_is_lazy_and_preserves_exact_corpus(self):
        from shared_player.gameplay_export import preflight_gameplay

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("sys.modules", {"torch": None}),
        ):
            path, _, _, _ = producer_fixture(Path(directory))
            admitted = preflight_gameplay(path)
        self.assertEqual(admitted["completed_updates"], 4)
        self.assertEqual(
            admitted["expected_checkpoint_corpus"]["complete_training_games"], 2
        )
        self.assertEqual(len(admitted["journal_specs"]), 2)

    def test_failed_producer_is_rejected_even_when_result_hash_is_rebound(self):
        from shared_player.gameplay_export import preflight_gameplay

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("sys.modules", {"torch": None}),
        ):
            path, plan, result, _ = producer_fixture(Path(directory))
            result["status"] = "failed"
            target = Path(plan["inputs"]["pipeline_result"]["path"])
            target.write_text(json.dumps(result))
            plan["inputs"]["pipeline_result"]["sha256"] = hashlib.sha256(
                target.read_bytes()
            ).hexdigest()
            path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, "pipeline did not pass"):
                preflight_gameplay(path)

    def test_changed_retained_journal_is_rejected_before_model_import(self):
        from shared_player.gameplay_export import preflight_gameplay

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("sys.modules", {"torch": None}),
        ):
            path, _, _, episodes = producer_fixture(Path(directory))
            Path(episodes[0]["journal_path"]).write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "file changed"):
                preflight_gameplay(path)

    def test_extra_optimizer_results_cannot_invent_steps_for_one_collected_batch(self):
        from shared_player.gameplay_export import preflight_gameplay

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("sys.modules", {"torch": None}),
        ):
            path, plan, result, _ = producer_fixture(Path(directory))
            result["optimizer_results"] = [
                {
                    "finite_update": True,
                    "optimizer_steps": 2,
                    "tensor_sha256_after": "7" * 64,
                },
                {
                    "finite_update": True,
                    "optimizer_steps": 2,
                    "tensor_sha256_after": "c" * 64,
                },
            ]
            target = Path(plan["inputs"]["pipeline_result"]["path"])
            target.write_text(json.dumps(result))
            plan["inputs"]["pipeline_result"]["sha256"] = hashlib.sha256(
                target.read_bytes()
            ).hexdigest()
            path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, "match actual complete batches"):
                preflight_gameplay(path)

    def test_named_genuine_and_legacy_verifiers_cannot_substitute_or_default(self):
        from shared_player.compiler import artifact_provenance
        from shared_player.gameplay_export import admit_gameplay_identity

        metadata = admit_gameplay_identity(
            fictional_identity(),
            fictional_config(),
            training_manifest_sha256="a" * 64,
            controller_source_sha256="b" * 64,
            completed_updates=4,
        )
        metadata.update(checkpoint_sha256="9" * 64, export_sha256="8" * 64)
        canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        receipt = {
            "format": "witcher3-genuine-shared-export-v1",
            "data_kind": "gameplay",
            "checkpoint_rewritten": False,
            "tensor_identity_verified": True,
            "checkpoint_sha256": "9" * 64,
            "tensor_sha256": "c" * 64,
            "payload_sha256": "8" * 64,
            "metadata_identity_sha256": hashlib.sha256(canonical).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shared-export.json"
            path.write_text(json.dumps(receipt))
            metadata.update(
                shared_export_record="../shared-export.json",
                shared_export_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            manifest = {
                "purpose": "shared-player-gameplay-compile",
                "artifact_kind": "shared-gameplay",
            }
            kind, _ = artifact_provenance(manifest, metadata, path)
            self.assertEqual(kind, "shared-gameplay")
            for bad in (
                {"purpose": manifest["purpose"]},
                {"purpose": "unknown", "artifact_kind": "shared-gameplay"},
                {
                    "purpose": "shared-player-baseline-compile",
                    "artifact_kind": "legacy-baseline",
                },
            ):
                with self.subTest(manifest=bad), self.assertRaises(ValueError):
                    artifact_provenance(bad, metadata, path)
            forged = copy.deepcopy(metadata)
            forged["update_count"] = 99
            with self.assertRaisesRegex(ValueError, "metadata identity changed"):
                artifact_provenance(manifest, forged, path)


if __name__ == "__main__":
    unittest.main()
