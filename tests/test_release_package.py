import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import package_release
import policy_probes
import test_policy_bundle


class PlayerReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in package_release.SOURCE_FILES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Original mod source.\n")
        self.fixture = test_policy_bundle.PolicyBundleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        probes = self.root / "probes.json"
        card_ids, effects = policy_probes.fictional_vocabulary(
            self.fixture.metadata["card_count"]
        )
        states = policy_probes.synthetic_cases(
            card_ids,
            policy_probes.COUNT,
            policy_probes.SEED,
            dict(zip(card_ids, effects, strict=True)),
        )
        metadata = self.fixture.metadata
        header = {
            "fixture_schema": 1,
            "observation_schema_id": metadata["observation_schema_id"],
            "action_schema_id": metadata["action_schema_id"],
            "checkpoint_sha256": metadata["checkpoint_sha256"],
            "catalog_sha256": metadata["catalog_sha256"],
            "decision_source": policy_probes.SOURCE,
            "dev_game_count": 0,
            "synthetic_batch_size": 10,
            "rules_validated": False,
            "seed": policy_probes.SEED,
            "position_count": policy_probes.COUNT,
        }
        cases = []
        for index, state in enumerate(states):
            actions = state["legal_actions"]
            cases.append(
                {
                    "position_id": index,
                    "observation": state["observation"],
                    "legal_actions": actions,
                    "expected": {
                        "action_id": actions[0]["id"],
                        "group_logits": [0.0],
                        "local_logits": [0.0] * len(actions),
                        "observation_sha256": "0" * 64,
                        "action_features": [[0] * 5 for _ in actions],
                        "action_group_ids": [0] * len(actions),
                        "group_features": [[0, 0]],
                        "value": 0.0,
                        "outcome_probabilities": [0.0, 0.0, 1.0],
                    },
                }
            )
        probes.write_text(
            json.dumps(
                {
                    "format": policy_probes.FORMAT,
                    "models": {
                        metadata["checkpoint_sha256"]: {
                            "vocabulary": policy_probes.VOCABULARY,
                            "card_ids": card_ids,
                            "card_effects": effects,
                            "header": header,
                            "cases": cases,
                        }
                    },
                }
            )
        )
        self.policies = self.fixture.create(probes_path=probes)

    def test_archive_has_only_allowlisted_source_and_validated_policy_files(self):
        hidden = self.source / ".runtime/private-record.jsonl"
        hidden.parent.mkdir()
        hidden.write_text("must stay local")
        output = self.root / "player.zip"
        result = package_release.create_release(self.source, self.policies, output)
        self.assertEqual(
            result["sha256"], hashlib.sha256(output.read_bytes()).hexdigest()
        )
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            names = archive.namelist()
            self.assertNotIn(".runtime/private-record.jsonl", names)
            self.assertEqual(set(package_release.SOURCE_FILES) - set(names), set())
            self.assertIn("policies/manifest.json", names)
            self.assertIn("policies/verification/probes.json", names)
            self.assertTrue(any(name.startswith("policies/weights/") for name in names))
            manifest = json.loads(archive.read("release-manifest.json"))
            self.assertEqual(manifest["format"], "witcher3-player-release-v1")
            self.assertEqual(
                set(manifest["files"]), set(names) - {"release-manifest.json"}
            )
            for name, entry in manifest["files"].items():
                self.assertEqual(
                    entry["sha256"], hashlib.sha256(archive.read(name)).hexdigest()
                )
                self.assertEqual(entry["bytes"], len(archive.read(name)))
        self.assertTrue(output.with_suffix(".zip.sha256").is_file())

    def test_missing_policy_verification_blocks_the_release(self):
        bundle = self.fixture.create("without-probes")
        with self.assertRaisesRegex(package_release.ReleaseError, "verification"):
            package_release.create_release(
                self.source, bundle, self.root / "player.zip"
            )
        self.assertFalse((self.root / "player.zip").exists())

    def test_modified_weights_block_the_release(self):
        path = next((self.policies / "weights").glob("*.bin"))
        path.write_bytes(b"not model parameters")
        with self.assertRaises(package_release.ReleaseError):
            package_release.create_release(
                self.source, self.policies, self.root / "player.zip"
            )
        self.assertFalse((self.root / "player.zip").exists())

    def test_source_symlinks_and_machine_references_block_the_release(self):
        name = package_release.SOURCE_FILES[0]
        source = self.source / name
        source.unlink()
        outside = self.root / "outside"
        outside.write_text("foreign")
        source.symlink_to(outside)
        with self.assertRaisesRegex(package_release.ReleaseError, "symlink"):
            package_release.create_release(
                self.source, self.policies, self.root / "player.zip"
            )
        source.unlink()
        source.write_text("path = '" + "/" + "home/Someone/local-file'\n")
        with self.assertRaisesRegex(package_release.ReleaseError, "local reference"):
            package_release.create_release(
                self.source, self.policies, self.root / "player.zip"
            )

    def test_existing_download_is_not_overwritten(self):
        output = self.root / "player.zip"
        output.write_bytes(b"preserve previous download")
        with self.assertRaisesRegex(package_release.ReleaseError, "already exists"):
            package_release.create_release(self.source, self.policies, output)
        self.assertEqual(output.read_bytes(), b"preserve previous download")


if __name__ == "__main__":
    unittest.main()
