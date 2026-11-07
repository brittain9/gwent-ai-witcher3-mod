from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

from learning.catalog_identity import catalog_identity
from learning.encoding import ACTION_KINDS, encode_batch
from scripts import create_policy_fixtures, export_policy_as3
from tests.synthetic_policy import CARD_IDS, exportable_checkpoint, synthetic_suite

ROOT = Path(__file__).resolve().parents[1]
REAL_CHECKPOINT = Path(
    os.environ.get("W3_FIXTURE_CHECKPOINT", ROOT / ".runtime/models/hard.pt")
)
REAL_CATALOG = Path(
    os.environ.get("W3_FIXTURE_CATALOG", ROOT / ".runtime/catalog.json")
)


class PortableFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="w3-portable-fixtures-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        _, catalog = synthetic_suite()
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps(catalog))
        self.checkpoint = exportable_checkpoint(
            self.root / "checkpoint.pt", self.catalog, seed=31
        )
        prior_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        self.addCleanup(torch.set_num_threads, prior_threads)

    def test_schema_coverage_is_varied_and_has_no_hidden_opponent_cards(self) -> None:
        cases = create_policy_fixtures.synthetic_cases(CARD_IDS, count=200, seed=19)
        encoded = encode_batch(cases, CARD_IDS)
        self.assertTrue(np.isfinite(encoded.observations).all())
        self.assertEqual({case["observation"]["seat"] for case in cases}, {0, 1})
        self.assertEqual({case["observation"]["round"] for case in cases}, {1, 2, 3})
        self.assertEqual(
            {action["kind"] for case in cases for action in case["legal_actions"]},
            set(ACTION_KINDS),
        )
        self.assertEqual(
            {
                None
                if case["observation"]["pending_choice"] is None
                else case["observation"]["pending_choice"]["kind"]
                for case in cases
            },
            set(create_policy_fixtures.PENDING_KINDS),
        )
        zones = encoded.observations[:, : 18 * len(CARD_IDS)].reshape(
            200, 18, len(CARD_IDS)
        )
        self.assertTrue(
            (zones.sum(axis=(0, 2)) > 0).all(), "every schema zone must be exercised"
        )
        self.assertEqual(
            {
                player["passed"]
                for case in cases
                for player in case["observation"]["players"]
            },
            {False, True},
        )
        self.assertEqual(
            {tuple(case["observation"]["lives"]) for case in cases},
            {(1, 1), (1, 2), (2, 1), (2, 2)},
        )
        for case in cases:
            observation = case["observation"]
            self.assertEqual(
                observation["players"][observation["seat"]]["hand_count"],
                len(observation["hand"]),
            )
            for player in observation["players"]:
                self.assertTrue(
                    {"hand", "ai_hand", "deck", "deck_order"}.isdisjoint(player)
                )

    def test_generation_is_deterministic_and_explicitly_synthetic(self) -> None:
        outputs = [self.root / f"case-{index}.jsonl" for index in range(3)]
        summaries = [
            create_policy_fixtures.create_fixtures(
                checkpoint_path=self.checkpoint,
                catalog_path=self.catalog,
                output_path=output,
                count=24,
                seed=8 if index < 2 else 9,
                env_count=4,
            )
            for index, output in enumerate(outputs)
        ]
        self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
        self.assertNotEqual(outputs[0].read_bytes(), outputs[2].read_bytes())
        header = json.loads(outputs[0].read_text().split("\n")[0])
        self.assertEqual(header["position_count"], 24)
        self.assertEqual(
            header["decision_source"], create_policy_fixtures.DECISION_SOURCE
        )
        self.assertEqual(header["dev_game_count"], 0)
        self.assertEqual(header["synthetic_batch_size"], 4)
        self.assertIs(header["rules_validated"], False)
        self.assertEqual(
            header["catalog_sha256"],
            catalog_identity(json.loads(self.catalog.read_bytes())),
        )
        self.assertNotIn("engine_binding_sha256", header)
        self.assertEqual(summaries[0]["count"], 24)
        self.assertGreater(
            summaries[0]["legal_action_max"], summaries[0]["legal_action_min"]
        )

    def test_catalog_mismatch_cannot_overwrite_existing_evidence(self) -> None:
        wrong = self.root / "wrong.json"
        _, catalog = synthetic_suite()
        catalog["cards"][0]["effects"] = [1]
        wrong.write_text(json.dumps(catalog))
        output = self.root / "protected.jsonl"
        output.write_text("preserved")
        with self.assertRaisesRegex(ValueError, "catalog hash"):
            create_policy_fixtures.create_fixtures(
                checkpoint_path=self.checkpoint,
                catalog_path=wrong,
                output_path=output,
                count=1,
            )
        self.assertEqual(output.read_text(), "preserved")

    def test_invalid_generation_bounds_fail_before_reading_inputs(self) -> None:
        for overrides in (
            {"count": 0},
            {"count": True},
            {"env_count": 0},
            {"env_count": 11},
            {"seed": 1.5},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                create_policy_fixtures.create_fixtures(
                    checkpoint_path=self.root / "absent.pt",
                    catalog_path=self.root / "absent.json",
                    output_path=self.root / "absent.jsonl",
                    **overrides,
                )

    def test_fixture_generation_does_not_import_a_game_engine(self) -> None:
        harness = """
import importlib.abc,sys
from pathlib import Path
class NoEngine(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname in {"learning.native","learning.batch_env","w3gwent.engine","w3gwent.match_runner"}:
   raise AssertionError("fixture generation imported game engine: "+fullname)
sys.meta_path.insert(0,NoEngine())
from scripts.create_policy_fixtures import create_fixtures
summary=create_fixtures(checkpoint_path=Path(sys.argv[1]),catalog_path=Path(sys.argv[2]),output_path=Path(sys.argv[3]),count=16)
assert summary["count"]==16
"""
        import sys

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                harness,
                str(self.checkpoint),
                str(self.catalog),
                str(self.root / "engine-free.jsonl"),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is required for AS3 source replay")
    def test_repeated_template_fractional_power_matches_both_encoders_exactly(
        self,
    ) -> None:
        case = create_policy_fixtures.synthetic_cases(CARD_IDS, count=1, seed=1)[0]
        observation = case["observation"]
        observation["players"][observation["seat"]]["rows"]["melee"]["cards"] = [
            {
                "instance_id": 100 + i,
                "template_id": CARD_IDS[0],
                "power": score,
                "score": score,
                "effects": [],
            }
            for i, score in enumerate((19, 21))
        ]
        expected = encode_batch([case], CARD_IDS).observations[0]
        encoded_path = self.root / "observation.json"
        encoded_path.write_text(json.dumps(observation))
        script = """
const fs=require('node:fs');const {loadNetworkPolicy}=require('./mod/ai/node/replay.js');
const ids=[9001,9002];const policy=loadNetworkPolicy(fs.readFileSync('mod/ai/as3/GwaiNetPolicy.as','utf8'),fs.readFileSync('mod/ai/as3/GwaiPolicyCore.as','utf8'),{card_ids:ids,card_effects:[[],[]],parameter_count:0},[]);
const observation=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
process.stdout.write(JSON.stringify(policy.encodeObservation(observation,ids,[[],[]]).map(Math.fround)));
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script, str(encoded_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        np.testing.assert_array_equal(
            expected, np.array(json.loads(result.stdout), dtype=np.float32)
        )
        self.assertEqual(
            float(expected[18 * len(CARD_IDS)]),
            float(np.float32(np.float32(0.19) + np.float32(0.21))),
        )

    def replay(self, checkpoint: Path, catalog: Path) -> dict:
        output = self.root / "fixtures.jsonl"
        summary = create_policy_fixtures.create_fixtures(
            checkpoint_path=checkpoint,
            catalog_path=catalog,
            output_path=output,
            count=200,
            seed=20261103,
            env_count=10,
        )
        self.assertEqual(summary["count"], 200)
        export = self.root / "export"
        export_policy_as3.export(checkpoint, catalog, export)
        result = subprocess.run(
            [
                shutil.which("node"),
                str(ROOT / "mod/ai/node/replay.js"),
                "--network",
                str(ROOT / "mod/ai/as3/GwaiNetPolicy.as"),
                "--core",
                str(ROOT / "mod/ai/as3/GwaiPolicyCore.as"),
                "--record",
                str(output),
                "--metadata",
                str(export / "metadata.json"),
                "--weights",
                str(export / "policy.f32.bin"),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        replay = json.loads(result.stdout)
        self.assertEqual(replay["total"], 200)
        self.assertEqual(replay["agreement"], 200)
        self.assertEqual(replay["decisionAgreement"], 200)
        self.assertEqual(replay["decisionFallbacks"], 0)
        self.assertEqual(replay["observationMismatches"], 0)
        self.assertEqual(replay["actionEncodingMismatches"], 0)
        for key in ("encoderPass", "logitsPass", "valuePass"):
            self.assertIs(replay[key], True, key)
        return replay

    @unittest.skipUnless(shutil.which("node"), "Node is required for AS3 source replay")
    def test_200_synthetic_cases_match_actual_as3_policy_and_encoder(self) -> None:
        self.replay(self.checkpoint, self.catalog)

    @unittest.skipUnless(
        shutil.which("node") and REAL_CHECKPOINT.is_file() and REAL_CATALOG.is_file(),
        "optional local trained checkpoint/catalog are absent",
    )
    def test_200_cases_match_the_local_trained_checkpoint(self) -> None:
        self.replay(REAL_CHECKPOINT, REAL_CATALOG)


if __name__ == "__main__":
    unittest.main()
