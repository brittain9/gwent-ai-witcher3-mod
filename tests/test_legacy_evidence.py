"""Historical gameplay evidence never becomes new-controller training evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


def evidence():
    original, relocated, source = "a" * 64, "b" * 64, "c" * 64
    inventory = {
        "data_kind": "gameplay",
        "inputs": {
            "original_checkpoint": {"sha256": original},
            "relocated_checkpoint": {"sha256": relocated},
            "training_run": {"sha256": "d" * 64},
        },
        "historical_training": {
            "run_id": "historical-gameplay-run",
            "code_tree_sha256": source,
            "source_file_sha256": {"learning/model.py": "e" * 64},
            "counter": "accepted engine actions including pending subchoices",
            "controller": "historical all-neural pending choices",
            "observation_profile": "fast faction-zero observations",
        },
    }
    run = {
        "run_id": "historical-gameplay-run",
        "code_tree_sha256": source,
        "source_file_sha256": {"learning/model.py": "e" * 64},
        "fast_observations": True,
        "games_completed": 100,
        "update_offset": 1000,
        # The final snapshot deliberately differs from the selected snapshot.
        "checkpoint_sha256": "f" * 64,
    }
    index = [{"sha256": original, "updates": 75, "games_completed": 50}]
    metadata = {"checkpoint_sha256": relocated, "update_count": 1075}
    relocation = [{"previous_sha256": original, "sha256": relocated, "tier": "hard"}]
    return inventory, run, index, metadata, relocation


class LegacyEvidenceTests(unittest.TestCase):
    def test_build_requires_matching_conversion_receipt_and_explicit_scopes(self):
        from shared_player.legacy import (
            LegacyEvidenceError,
            admit_legacy_records,
            derive_serving_metadata,
            verify_baseline_binding,
        )

        args = evidence()
        admitted = admit_legacy_records(*args)
        metadata = derive_serving_metadata(args[3], admitted)
        receipt = {
            **admitted,
            "format": "witcher3-explicit-legacy-serving-conversion-v1",
            "serving_contract_scope": "derived-serving-only-not-historical-training",
            "tensor_identity_verified": True,
            "fresh_tensor_payload_equals_original_export": True,
            "checkpoint_rewritten": False,
            "new_training_performed": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversion.json"
            path.write_text(json.dumps(receipt))
            metadata["baseline_conversion_record"] = "../conversion.json"
            metadata["baseline_conversion_sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            bound = verify_baseline_binding(metadata, path)
            self.assertEqual(
                bound["conversion_sha256"], metadata["baseline_conversion_sha256"]
            )
            self.assertFalse(bound["trained_with_serving_controller"])
            tampered = copy.deepcopy(metadata)
            del tampered["controller_source_scope"]
            with self.assertRaises(LegacyEvidenceError):
                verify_baseline_binding(tampered, path)
            path.write_text(json.dumps({**receipt, "new_training_performed": True}))
            with self.assertRaises(LegacyEvidenceError):
                verify_baseline_binding(metadata, path)

    def test_derived_metadata_preserves_historical_training_scope(self):
        from shared_player.legacy import admit_legacy_records, derive_serving_metadata

        arguments = evidence()
        admitted = admit_legacy_records(*arguments)
        result = derive_serving_metadata(arguments[3], admitted)
        self.assertEqual(
            result["controller_source_scope"], "historical-training-code-tree-v1"
        )
        self.assertEqual(
            result["serving_contract_scope"],
            "derived-serving-only-not-historical-training",
        )
        self.assertFalse(result["trained_with_serving_controller"])
        self.assertEqual(
            result["historical_training"], arguments[0]["historical_training"]
        )
        self.assertEqual(result["selected_snapshot_sha256"], "a" * 64)
        self.assertEqual(result["relocated_checkpoint_sha256"], "b" * 64)
        self.assertNotIn("controller_source_scope", arguments[3])

    def test_selected_snapshot_and_historical_source_are_preserved(self):
        from shared_player.legacy import admit_legacy_records

        inventory, run, index, metadata, relocation = evidence()
        result = admit_legacy_records(inventory, run, index, metadata, relocation)
        self.assertEqual(result["data_kind"], "gameplay")
        self.assertEqual(result["training_manifest_sha256"], "d" * 64)
        self.assertEqual(result["controller_source_sha256"], "c" * 64)
        self.assertEqual(
            result["historical_training"], inventory["historical_training"]
        )
        self.assertFalse(result["trained_with_serving_controller"])
        self.assertEqual(result["selected_snapshot_sha256"], "a" * 64)

    def test_missing_or_synthetic_origin_and_wrong_snapshot_are_refused(self):
        from shared_player.legacy import LegacyEvidenceError, admit_legacy_records

        for kind in (None, "synthetic", "unknown"):
            arguments = list(evidence())
            arguments[0]["data_kind"] = kind
            with self.subTest(kind=kind), self.assertRaises(LegacyEvidenceError):
                admit_legacy_records(*arguments)
        arguments = list(evidence())
        arguments[2][0]["sha256"] = "9" * 64
        with self.assertRaises(LegacyEvidenceError):
            admit_legacy_records(*arguments)

    def test_training_source_and_selected_update_must_match_original_record(self):
        from shared_player.legacy import LegacyEvidenceError, admit_legacy_records

        for field, value in (
            ("code_tree_sha256", "8" * 64),
            ("source_file_sha256", {}),
        ):
            arguments = list(evidence())
            arguments[1][field] = value
            with self.subTest(field=field), self.assertRaises(LegacyEvidenceError):
                admit_legacy_records(*arguments)
        arguments = list(evidence())
        arguments[3]["update_count"] = 1076
        with self.assertRaises(LegacyEvidenceError):
            admit_legacy_records(*arguments)

    def test_missing_relocation_and_duplicate_selected_snapshot_are_refused(self):
        from shared_player.legacy import LegacyEvidenceError, admit_legacy_records

        arguments = list(evidence())
        arguments[4] = []
        with self.assertRaises(LegacyEvidenceError):
            admit_legacy_records(*arguments)
        arguments = list(evidence())
        arguments[2].append(copy.deepcopy(arguments[2][0]))
        with self.assertRaises(LegacyEvidenceError):
            admit_legacy_records(*arguments)


if __name__ == "__main__":
    unittest.main()
