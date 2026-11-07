"""The final compiled header, rather than its claimed identity, must match."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts import compiled_policy, policy_format


class CompiledPolicyTests(unittest.TestCase):
    def setUp(self):
        self.name = "GwaiNetWeightsData_" + "a" * 16
        payload = b"\x00\x00\x00\x00\x00\x00\x80?"
        self.metadata = {
            "checkpoint_sha256": "a" * 64,
            "update_count": 12,
            "export_sha256": hashlib.sha256(payload).hexdigest(),
            "catalog_sha256": "b" * 64,
            "parameter_count": 2,
            "observation_size": 922,
            "card_count": 2,
            "width": 8,
            "blocks": 1,
            "offsets": {"input.weight": 0},
            "card_ids": [7, 13],
            "card_effects": [[1], [2, 3]],
            "weight_bytes": len(payload),
        }
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "header.as"
            policy_format.write_embed_header(path, payload, self.metadata, self.name)
            self.source = path.read_text()

    def verify(self, source):
        return compiled_policy.verify({self.name: source}, {self.name: self.metadata})

    def test_generated_header_is_verified(self):
        result = self.verify(self.source)
        self.assertTrue(result["verified"])
        self.assertEqual(result["models"][0]["sha256"], self.metadata["export_sha256"])

    def test_tampered_payload_with_unchanged_identity_is_refused(self):
        self.assertIn('"AAAAAAAAgD8="', self.source)
        with self.assertRaisesRegex(ValueError, "tensor bytes differ"):
            self.verify(self.source.replace('"AAAAAAAAgD8="', '"BAAAAAAAgD8="'))

    def test_runtime_vocabulary_effects_offsets_and_geometry_must_match(self):
        for before, after in [
            ("[7,13]", "[7,14]"),
            ("[[1],[2,3]]", "[[true],[2,3]]"),
            ("[[1],[2,3]]", "[[1],[2,4]]"),
            ('"input.weight":0', '"input.weight":1'),
            ("OBSERVATION_SIZE:int = 922", "OBSERVATION_SIZE:int = 923"),
        ]:
            with self.subTest(before=before):
                self.assertIn(before, self.source)
                with self.assertRaisesRegex(ValueError, "differs in"):
                    self.verify(self.source.replace(before, after))

    def test_joined_payload_identity_cannot_hide_invalid_chunk_boundaries(self):
        with self.assertRaisesRegex(ValueError, "invalid base64"):
            self.verify(self.source.replace('"AAAAAAAAgD8="', '"AAAAAAA","AgD8="'))
        with self.assertRaisesRegex(ValueError, "invalid base64"):
            self.verify(self.source.replace('"AAAAAAAAgD8="', '"AAAA","AAAAgD8="'))

    def test_duplicate_or_missing_data_class_is_refused(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.verify(self.source + "\npublic static const WIDTH:int = 8;")
        with self.assertRaisesRegex(ValueError, "inventory"):
            compiled_policy.verify({}, {self.name: self.metadata})


if __name__ == "__main__":
    unittest.main()
