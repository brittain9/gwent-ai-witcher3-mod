"""A model's input contract must not change the captured game state."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

from learning.encoding import encode_batch
from learning.policy import checkpoint_policy
from scripts import export_policy_as3, policy_format, policy_probes
from scripts.create_policy_fixtures import _logits_and_choices
from tests.synthetic_policy import (
    CARD_IDS,
    decision,
    exportable_checkpoint,
    synthetic_suite,
)

ROOT = Path(__file__).resolve().parents[1]
FULL = policy_format.FULL_FACTION_PROFILE
UNKNOWN = policy_format.UNKNOWN_FACTION_PROFILE


class ObservationProfileEncodingTests(unittest.TestCase):
    def test_unknown_faction_matches_training_without_mutating_capture(self):
        observation, actions = decision()
        for seat in (0, 1):
            for own, opponent in ((3, 3), (1, 5), (0, 4)):
                with self.subTest(seat=seat, own=own, opponent=opponent):
                    native = copy.deepcopy(observation)
                    native["seat"] = seat
                    by_seat = {player["seat"]: player for player in native["players"]}
                    by_seat[seat]["faction"] = own
                    by_seat[1 - seat]["faction"] = opponent
                    original = copy.deepcopy(native)
                    historical = copy.deepcopy(native)
                    for player in historical["players"]:
                        player.pop("faction")
                    state = {"observation": native, "legal_actions": actions}
                    actual = encode_batch(
                        [state], CARD_IDS, observation_profile_id=UNKNOWN
                    )
                    expected = encode_batch(
                        [{"observation": historical, "legal_actions": actions}],
                        CARD_IDS,
                    )
                    for field in (
                        "observations",
                        "action_features",
                        "group_features",
                        "action_group_ids",
                        "legal_mask",
                        "group_mask",
                    ):
                        np.testing.assert_array_equal(
                            getattr(actual, field), getattr(expected, field)
                        )
                    self.assertEqual(native, original)
                    generic = encode_batch([state], CARD_IDS)
                    explicit = encode_batch(
                        [state], CARD_IDS, observation_profile_id=FULL
                    )
                    np.testing.assert_array_equal(
                        generic.observations, explicit.observations
                    )
                    globals_ = generic.observations[0, -64:]
                    self.assertEqual(
                        globals_[28:34].tolist(), [float(i == own) for i in range(6)]
                    )
                    self.assertEqual(
                        globals_[39:45].tolist(),
                        [float(i == opponent) for i in range(6)],
                    )

    def test_unknown_profiles_are_rejected_even_for_empty_batches(self):
        for profile in (None, "faction-unknown-v2", [], 0):
            with (
                self.subTest(profile=profile),
                self.assertRaisesRegex(ValueError, "profile"),
            ):
                encode_batch([], CARD_IDS, observation_profile_id=profile)

    def test_legacy_migration_requires_explicit_request_and_exact_checkpoint(self):
        for checkpoint in policy_format.FACTION_UNKNOWN_CHECKPOINTS:
            self.assertEqual(
                policy_format.checkpoint_observation_profile(checkpoint, {}), FULL
            )
            self.assertEqual(
                policy_format.checkpoint_observation_profile(checkpoint, {}, UNKNOWN),
                UNKNOWN,
            )
        with self.assertRaisesRegex(ValueError, "not audited"):
            policy_format.checkpoint_observation_profile("0" * 64, {}, UNKNOWN)
        with self.assertRaisesRegex(ValueError, "not audited"):
            policy_format.checkpoint_observation_profile(
                next(iter(policy_format.FACTION_UNKNOWN_CHECKPOINTS)),
                {"observation_profile_id": FULL},
                UNKNOWN,
            )
        self.assertEqual(policy_format.observation_profile({}), FULL)
        self.assertEqual(
            policy_format.observation_profile({"observation_profile_id": UNKNOWN}),
            UNKNOWN,
        )
        with self.assertRaisesRegex(ValueError, "profile"):
            policy_format.observation_profile({"observation_profile_id": None})


class ObservationProfilePolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        _, self.catalog = synthetic_suite()
        self.catalog_path = self.root / "catalog.json"
        self.catalog_path.write_text(json.dumps(self.catalog))
        self.legacy = exportable_checkpoint(
            self.root / "legacy.pt", self.catalog_path, seed=17
        )
        checkpoint = torch.load(self.legacy, map_location="cpu", weights_only=False)
        checkpoint["model_config"]["observation_profile_id"] = UNKNOWN
        self.profiled = self.root / "profiled.pt"
        torch.save(checkpoint, self.profiled)
        previous = torch.get_num_threads()
        torch.set_num_threads(1)
        self.addCleanup(torch.set_num_threads, previous)

    def load(self, path, **kwargs):
        return checkpoint_policy(
            path,
            self.catalog,
            catalog_sha256=policy_format.catalog_identity(self.catalog),
            **kwargs,
        )

    def test_policy_served_choice_and_logits_match_historical_input(self):
        generic, profiled = self.load(self.legacy), self.load(self.profiled)
        observation, actions = decision()
        for player in observation["players"]:
            player["faction"] = 3
        original = copy.deepcopy(observation)
        historical = copy.deepcopy(observation)
        for player in historical["players"]:
            player.pop("faction")
        actual = profiled._encode([observation], [actions])
        expected = generic._encode([historical], [actions])
        np.testing.assert_array_equal(actual.observations, expected.observations)
        self.assertEqual(
            profiled.served_act_many([observation], [actions]),
            generic.served_act_many([historical], [actions]),
        )
        native_logits = _logits_and_choices(
            profiled, [{"observation": observation, "legal_actions": actions}], CARD_IDS
        )
        historical_logits = _logits_and_choices(
            generic, [{"observation": historical, "legal_actions": actions}], CARD_IDS
        )
        self.assertEqual(native_logits, historical_logits)
        self.assertEqual(observation, original)
        self.assertEqual(generic.observation_profile_id, FULL)
        self.assertEqual(profiled.observation_profile_id, UNKNOWN)

    def test_export_declares_profile_and_preserves_every_tensor_byte(self):
        generic = export_policy_as3.export(
            self.legacy, self.catalog_path, self.root / "generic"
        )
        profiled = export_policy_as3.export(
            self.profiled, self.catalog_path, self.root / "profiled"
        )
        self.assertEqual(generic["observation_profile_id"], FULL)
        self.assertEqual(profiled["observation_profile_id"], UNKNOWN)
        self.assertEqual(
            (self.root / "generic/policy.f32.bin").read_bytes(),
            (self.root / "profiled/policy.f32.bin").read_bytes(),
        )
        self.assertEqual(generic["weights_sha256"], profiled["weights_sha256"])
        header = (self.root / "profiled/GwaiNetWeightsData.as").read_text()
        self.assertIn(f'OBSERVATION_PROFILE_ID:String = "{UNKNOWN}"', header)
        with self.assertRaisesRegex(ValueError, "not audited"):
            self.load(self.legacy, observation_profile_id=UNKNOWN)
        with self.assertRaisesRegex(ValueError, "not audited"):
            export_policy_as3.export(
                self.legacy,
                self.catalog_path,
                self.root / "refused",
                observation_profile_id=UNKNOWN,
            )
        self.assertFalse((self.root / "refused").exists())

    @unittest.skipUnless(
        shutil.which("node"), "Node.js is required for the AS boundary"
    )
    def test_profiled_fictional_probes_match_actual_as_encoder_and_decision(self):
        metadata = export_policy_as3.export(
            self.profiled, self.catalog_path, self.root / "export"
        )
        probes = self.root / "probes.json"
        policy_probes.create_probes({"hard": self.profiled}, self.catalog_path, probes)
        checks = policy_probes.prepare_checks(probes, metadata, self.root / "checks")
        result = subprocess.run(
            [
                "node",
                str(ROOT / "mod/ai/node/replay.js"),
                "--network",
                str(ROOT / "mod/ai/as3/GwaiNetPolicy.as"),
                "--record",
                checks["path"],
                "--metadata",
                checks["metadata_path"],
                "--weights",
                str(self.root / "export/policy.f32.bin"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["agreement"], 200)
        self.assertEqual(summary["decisionAgreement"], summary["decisionTotal"])
        self.assertEqual(summary["decisionFallbacks"], 0)
        self.assertTrue(summary["encoderPass"])
        self.assertTrue(summary["valuePass"])
        self.assertTrue(summary["logitsPass"])
        metadata_path = Path(checks["metadata_path"])
        saved = json.loads(metadata_path.read_text())
        for profile, message in (
            ("unrecognized-v1", "unsupported model observation profile"),
            (FULL, "different observation profile"),
        ):
            metadata_path.write_text(
                json.dumps({**saved, "observation_profile_id": profile})
            )
            refused = subprocess.run(
                [
                    "node",
                    str(ROOT / "mod/ai/node/replay.js"),
                    "--network",
                    str(ROOT / "mod/ai/as3/GwaiNetPolicy.as"),
                    "--record",
                    checks["path"],
                    "--metadata",
                    str(metadata_path),
                    "--weights",
                    str(self.root / "export/policy.f32.bin"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn(message, refused.stderr)
        with self.assertRaisesRegex(ValueError, "profile|identity"):
            policy_probes.prepare_checks(
                probes,
                {**metadata, "observation_profile_id": FULL},
                self.root / "wrong",
            )
