"""Native numeric field mapping and exact served-action diagnostics."""

import copy
import hashlib
import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from test_shared_player_bundle import synthetic_export


def numeric_example():
    expected = {
        "observation_sha256": hashlib.sha256(struct.pack("<ff", 0.25, 1)).hexdigest(),
        "action_features": [[1, 2], [1, 2]],
        "group_features": [[1, 2], [1, 2]],
        "action_group_ids": [0, 1],
        "group_logits": [0.1, 0.1],
        "local_logits": [0.2, 0.2],
        "value": 0.7,
        "outcome_probabilities": [0.2, 0.2, 0.6],
        "chosen_index": 0,
    }
    native = {
        "observation_features": [0.25, 1],
        "action_features": expected["action_features"],
        "group_features": expected["group_features"],
        "action_groups": [0, 1],
        "group_logits": [0.1, 0.1],
        "local_logits": [0.2, 0.2],
        "probabilities": [0.5, 0.5],
        "critic_probabilities": [0.2, 0.2, 0.6],
        "critic_class_order": ["loss", "draw", "win"],
        "value": 0.7,
        "served_action_index": 0,
    }
    return native, expected


class NativeNumericTests(unittest.TestCase):
    def test_primary_subset_preserves_inputs_and_accounts_native_pending_omissions(
        self,
    ):
        from shared_player.native_numeric import primary_case_plan

        records = [
            {
                "position_id": index,
                "observation": {
                    "pending_choice": None if index % 8 == 0 else {"kind": "nurse"}
                },
                "legal_actions": [{"id": str(index)}],
            }
            for index in range(200)
        ]
        data = {
            "format": "witcher3-policy-probes-v1",
            "models": {"model": {"cases": records}},
        }
        original = copy.deepcopy(data)
        plan = primary_case_plan(data, "model")
        self.assertEqual(len(plan["selected"]), 25)
        self.assertEqual(len(plan["omitted"]), 175)
        self.assertEqual(
            [item["source_index"] for item in plan["selected"]], list(range(0, 200, 8))
        )
        self.assertEqual(data, original)
        self.assertIs(plan["selected"][0]["record"], records[0])
        self.assertEqual(
            plan["omitted"][0]["reason"], "native-pending-choice-not-primary"
        )

    def test_actual_primary_dataset_cannot_silently_omit_a_pending_case(self):
        from shared_player.native_numeric import NativeNumericError, primary_case_plan

        data = {
            "format": "witcher3-shared-actual-primary-probes-v1",
            "models": {
                "model": {
                    "header": {"position_count": 1},
                    "cases": [
                        {
                            "position_id": 9,
                            "observation": {"pending_choice": {"kind": "nurse"}},
                        }
                    ],
                }
            },
        }
        with self.assertRaisesRegex(NativeNumericError, "actual-primary"):
            primary_case_plan(data, "model")
        data["models"]["model"]["cases"][0]["observation"]["pending_choice"] = None
        plan = primary_case_plan(data, "model")
        self.assertEqual(len(plan["selected"]), 1)
        self.assertEqual(plan["omitted"], [])

    def test_fictional_vocabulary_descriptor_fails_player_admission(self):
        from shared_player.bundle import (
            METADATA_FIELDS,
            SharedBundleError,
            validate_metadata,
        )
        from shared_player.native_numeric import numeric_fixture_metadata

        with tempfile.TemporaryDirectory() as directory:
            exported = synthetic_export(Path(directory) / "export")
            original = json.loads((exported / "metadata.json").read_text())
            derived = numeric_fixture_metadata(
                original,
                card_ids=[1, 2],
                card_effects=[[], [4]],
                source_metadata_sha256="d" * 64,
            )
            self.assertEqual(original["data_kind"], "gameplay")
            self.assertEqual(
                derived["checkpoint_sha256"], original["checkpoint_sha256"]
            )
            self.assertEqual(derived["weights_sha256"], original["weights_sha256"])
            self.assertEqual(derived["original_model_data_kind"], "gameplay")
            self.assertTrue(derived["numeric_fixture_only"])
            with self.assertRaises(SharedBundleError):
                validate_metadata({field: derived[field] for field in METADATA_FIELDS})

    def test_policy_value_encoder_and_served_index_are_compared(self):
        from shared_player.native_numeric import compare_native

        native, expected = numeric_example()
        native["local_logits"][0] += 1e-6
        result = compare_native(native, expected)
        self.assertTrue(result["pass"])
        self.assertGreater(result["logits_max_abs_error"], 0)
        self.assertTrue(result["encoder_pass"])
        native["served_action_index"] = 1
        result = compare_native(native, expected)
        self.assertFalse(result["pass"])
        self.assertTrue(result["identical_feature_disagreement"])

    def test_nonfinite_missing_heads_or_changed_encoding_cannot_pass(self):
        from shared_player.native_numeric import compare_native

        native, expected = numeric_example()
        for field, value in (
            ("value", math.nan),
            ("critic_probabilities", None),
            ("group_logits", []),
            ("action_groups", [1, 0]),
            ("observation_features", [0.25, 0]),
        ):
            altered = copy.deepcopy(native)
            altered[field] = value
            with self.subTest(field=field):
                self.assertFalse(compare_native(altered, expected)["pass"])


if __name__ == "__main__":
    unittest.main()
