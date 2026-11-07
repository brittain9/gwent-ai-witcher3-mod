from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
BUILD_MANIFEST = ROOT / ".runtime/mod-build/manifest.json"


@unittest.skipUnless(NODE, "Node.js is required for fixture policy replay")
class ModPolicyReplayTests(unittest.TestCase):
    def test_engine_fixtures_replay_through_the_as3_network_source(self) -> None:
        if not BUILD_MANIFEST.is_file():
            self.skipTest("build the mod before replaying its checkpoint fixtures")
        manifest = json.loads(BUILD_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("status"), "offline_compiled_and_export_verified")
        policy_export = manifest["policy_exports"][0]
        export_dir = ROOT / ".runtime/policy-as3" / policy_export["checkpoint_sha256"]
        fixture_path = Path(policy_export["export_gate"]["path"])
        result = subprocess.run(
            [
                NODE,
                str(ROOT / "mod/ai/node/replay.js"),
                "--network",
                str(ROOT / "mod/ai/as3/GwaiNetPolicy.as"),
                "--metadata",
                str(export_dir / "metadata.json"),
                "--weights",
                str(export_dir / "policy.f32.bin"),
                "--record",
                str(fixture_path),
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["agreement"], summary["total"])
        self.assertGreaterEqual(summary["total"], 200)
        self.assertEqual(summary["decisionAgreement"], summary["decisionTotal"])
        self.assertEqual(summary["decisionFallbacks"], 0)
        self.assertEqual(
            summary["weightLoadCalls"],
            summary["decisionTotal"] + summary["insightTotal"],
        )
        self.assertTrue(summary["valuePass"])
        self.assertLessEqual(summary["valueMaxAbsError"], 1e-4)
        self.assertTrue(summary["encoderPass"])
        self.assertTrue(summary["logitsPass"])
        self.assertLessEqual(summary["logitsMaxAbsError"], 1e-4)

    def test_easy_sample_logit_matches_seeded_python_reference(self) -> None:
        if not BUILD_MANIFEST.is_file():
            self.skipTest("build the mod before checking the Easy sampler")
        manifest = json.loads(BUILD_MANIFEST.read_text(encoding="utf-8"))
        policy_export = manifest["policy_exports"][0]
        export_dir = ROOT / ".runtime/policy-as3" / policy_export["checkpoint_sha256"]
        fixture_path = Path(policy_export["export_gate"]["path"])
        seed = 20261026
        draws = 512
        result = subprocess.run(
            [
                NODE,
                str(ROOT / "mod/ai/node/replay.js"),
                "--network",
                str(ROOT / "mod/ai/as3/GwaiNetPolicy.as"),
                "--metadata",
                str(export_dir / "metadata.json"),
                "--weights",
                str(export_dir / "policy.f32.bin"),
                "--record",
                str(fixture_path),
                "--sampler-seed",
                str(seed),
                "--sampler-draws",
                str(draws),
                "--easy-temperature",
                "0.7",
            ],
            cwd=ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        actual = summary["samplerDraws"]
        self.assertEqual(summary["easyDecisionTotal"], summary["total"])
        self.assertEqual(summary["easyDecisionFallbacks"], 0)
        self.assertEqual(summary["easyDecisionInvalids"], 0)
        self.assertGreater(summary["easyRandomDraws"], 0)

        def reference() -> list[int]:
            state = seed & 0xFFFFFFFF
            choices = [0, 2]
            values = [1.75, -0.25, 0.5]
            temperature = 0.7
            maximum = max(values[index] for index in choices)
            weights = [
                math.exp((values[index] - maximum) / temperature) for index in choices
            ]
            total = sum(weights)
            result = []
            for _ in range(draws):
                state ^= (state << 13) & 0xFFFFFFFF
                state &= 0xFFFFFFFF
                state ^= state >> 17
                state &= 0xFFFFFFFF
                state ^= (state << 5) & 0xFFFFFFFF
                state &= 0xFFFFFFFF
                draw = (state / 0x100000000) * total
                for index, weight in enumerate(weights):
                    draw -= weight
                    if draw <= 0:
                        result.append(choices[index])
                        break
                else:
                    result.append(choices[-1])
            return result

        self.assertEqual(actual, reference())

    def test_candidate_budget_changes_the_selected_move(self) -> None:
        record = {
            "view": {
                "playerID": 1,
                "opponentID": 0,
                "scores": [0, 0],
                "opponentPassed": False,
            },
            "legal": [
                {"kind": "play", "instanceId": 1, "scoreGain": 2},
                {"kind": "play", "instanceId": 2, "scoreGain": 10},
            ],
            "expected": {"kind": "play", "instanceId": 1},
        }
        with tempfile.TemporaryDirectory(prefix="gwai-budget-") as temp_value:
            fixture = Path(temp_value) / "fixture.jsonl"
            fixture.write_text(json.dumps(record) + "\n", encoding="utf-8")
            limited = subprocess.run(
                [
                    NODE,
                    str(ROOT / "mod/ai/node/replay.js"),
                    "--record",
                    str(fixture),
                    "--limit",
                    "1",
                ],
                cwd=ROOT,
                capture_output=True,
                check=True,
                text=True,
            )
            unlimited = subprocess.run(
                [NODE, str(ROOT / "mod/ai/node/replay.js"), "--record", str(fixture)],
                cwd=ROOT,
                capture_output=True,
                check=True,
                text=True,
            )
        self.assertEqual(json.loads(limited.stdout), {"agreement": 1, "total": 1})
        self.assertEqual(json.loads(unlimited.stdout), {"agreement": 0, "total": 1})

    def test_policy_core_has_no_hidden_hand_or_deck_order_input(self) -> None:
        source = (ROOT / "mod/ai/as3/GwaiPolicyCore.as").read_text(encoding="utf-8")
        self.assertIn(
            "decide(view:Object, legalMoves:Array, difficulty:int = 2)", source
        )
        self.assertIn("view.opponentPassed", source)
        self.assertNotIn("opponentHand", source)
        self.assertNotIn("deckOrder", source)
        self.assertNotIn("Math.random", source)

    def test_policy_passes_only_when_a_passed_round_is_won_or_no_supported_move_exists(
        self,
    ) -> None:
        source = (ROOT / "mod/ai/as3/GwaiPolicyCore.as").read_text(encoding="utf-8")
        self.assertIn("view.opponentPassed && ownScore > opponentScore", source)
        self.assertIn("score >= 0 && (best == null || score > bestScore)", source)


if __name__ == "__main__":
    unittest.main()
