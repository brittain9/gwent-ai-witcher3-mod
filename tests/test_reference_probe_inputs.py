"""Reference model and recorded behavior identities remain explicitly distinct."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_primary_probe_inputs import fictional_journal


def fictional_plan(root):
    journal, behavior = fictional_journal(root)
    reference = root / "reference.pt"
    reference.write_bytes(b"fictional invalid reference checkpoint")
    catalog = root / "catalog.json"
    catalog.write_text('{"fictional":true}')
    plan = {
        "schema": 2,
        "purpose": "shared-player-reference-checkpoint-probes",
        "reference_checkpoint": {
            "path": str(reference),
            "sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        },
        "journal_behavior_checkpoint_sha256": behavior,
        "catalog": {
            "path": str(catalog),
            "sha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
        },
        "journals": [journal],
        "observation_profile_id": "faction-unknown-v1",
        "expected_primary_positions": 1,
    }
    path = root / "plan.json"
    path.write_text(json.dumps(plan))
    return path, plan


class ReferenceProbeInputTests(unittest.TestCase):
    def test_new_reference_keeps_authentic_old_behavior_and_primary_counter(self):
        from shared_player.primary_probes import preflight_reference_probes

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("sys.modules", {"torch": None}),
        ):
            path, plan = fictional_plan(Path(directory))
            admitted = preflight_reference_probes(path)
        self.assertNotEqual(
            plan["reference_checkpoint"]["sha256"],
            plan["journal_behavior_checkpoint_sha256"],
        )
        self.assertEqual(admitted["journal_behavior_checkpoint_sha256"], "a" * 64)
        self.assertEqual(
            admitted["reference_checkpoint_sha256"],
            plan["reference_checkpoint"]["sha256"],
        )
        self.assertEqual(
            admitted["positions"]["states"][0]["observation"]["decision_id"], 7
        )

    def test_missing_or_relabelled_behavior_identity_cannot_default_to_reference(self):
        from shared_player.primary_probes import preflight_reference_probes

        for alteration in (None, "reference"):
            with (
                self.subTest(alteration=alteration),
                tempfile.TemporaryDirectory() as directory,
            ):
                path, plan = fictional_plan(Path(directory))
                if alteration is None:
                    del plan["journal_behavior_checkpoint_sha256"]
                else:
                    plan["journal_behavior_checkpoint_sha256"] = plan[
                        "reference_checkpoint"
                    ]["sha256"]
                path.write_text(json.dumps(plan))
                with self.assertRaises(ValueError):
                    preflight_reference_probes(path)


if __name__ == "__main__":
    unittest.main()
