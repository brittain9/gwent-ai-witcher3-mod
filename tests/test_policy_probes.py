"""Public inference checks use fictional data and retain checkpoint parity."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch

from scripts import build_mod, export_policy_as3, policy_bundle, policy_probes
from tests.synthetic_policy import exportable_checkpoint, synthetic_suite

ROOT = Path(__file__).resolve().parents[1]


class PolicyProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        _, catalog = synthetic_suite()
        self.catalog = self.root / "catalog.json"
        self.catalog.write_text(json.dumps(catalog))
        self.checkpoint = exportable_checkpoint(
            self.root / "model.pt", self.catalog, seed=3
        )
        previous = torch.get_num_threads()
        torch.set_num_threads(1)
        self.addCleanup(torch.set_num_threads, previous)
        self.export = self.root / "export"
        self.metadata = export_policy_as3.export(
            self.checkpoint, self.catalog, self.export
        )
        self.probes = self.root / "probes.json"
        policy_probes.create_probes(
            {"hard": self.checkpoint}, self.catalog, self.probes
        )

    def test_probe_vocabulary_is_fictional_and_reference_matches_node(self):
        payload = json.loads(self.probes.read_text())
        item = payload["models"][self.metadata["checkpoint_sha256"]]
        self.assertEqual(
            item["card_ids"], list(range(1, self.metadata["card_count"] + 1))
        )
        self.assertNotEqual(item["card_ids"], self.metadata["card_ids"])
        output = self.root / "checks"
        checks = policy_probes.prepare_checks(self.probes, self.metadata, output)
        self.assertEqual(checks["count"], 200)
        self.assertEqual(
            json.loads((self.export / "metadata.json").read_text())["card_ids"],
            self.metadata["card_ids"],
            "fictional checks must not replace runtime metadata",
        )
        if shutil.which("node") is None:
            self.skipTest("Node.js is required for the ActionScript boundary")
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
                str(self.export / "policy.f32.bin"),
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["agreement"], 200)
        self.assertTrue(summary["valuePass"])

    def test_changed_case_and_extra_output_data_are_refused(self):
        original = self.probes.read_text()
        for mutate in [
            lambda item: item["card_ids"].__setitem__(0, 99999),
            lambda item: item["cases"][0]["observation"]["hand"][0].__setitem__(
                "name", "copied data"
            ),
            lambda item: item["cases"][0]["expected"].__setitem__(
                "notes", "private provenance"
            ),
            lambda item: item["cases"][0]["expected"].__setitem__(
                "value", float("nan")
            ),
        ]:
            payload = json.loads(original)
            mutate(payload["models"][self.metadata["checkpoint_sha256"]])
            self.probes.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                policy_probes.validate_model_probes(self.probes, self.metadata)

    def test_unlisted_model_cannot_hide_extra_material(self):
        payload = json.loads(self.probes.read_text())
        payload["models"]["unexpected"] = {"notes": "private"}
        self.probes.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "exactly"):
            policy_probes.validate_bundle_probes(self.probes, {"hard": self.metadata})

    def test_duplicate_keys_cannot_hide_overwritten_material(self):
        original = self.probes.read_text()
        self.probes.write_text('{"models":{"private":"hidden"},' + original[1:])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            policy_probes.validate_bundle_probes(self.probes, {"hard": self.metadata})

    def test_consumer_import_requires_only_the_standard_library(self):
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                "import scripts.policy_probes,sys; assert 'torch' not in sys.modules; assert 'numpy' not in sys.modules",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(
        shutil.which("node"), "Node.js is required for the builder gate"
    )
    def test_prepared_builder_gates_without_checkpoint_files(self):
        bundle_path = self.root / "bundle"
        policy_bundle.create_bundle(
            {tier: self.export for tier in build_mod.TIER_NAMES},
            bundle_path,
            probes_path=self.probes,
        )
        runtime = self.root / ".runtime"
        runtime.mkdir()
        shutil.copy2(self.catalog, runtime / "catalog.json")
        (self.root / "mod").symlink_to(ROOT / "mod", target_is_directory=True)
        with (
            mock.patch.object(build_mod, "ROOT", self.root),
            mock.patch.object(build_mod, "POLICY_EXPORT_DIR", runtime / "prepared"),
        ):
            sources, report, tiers = build_mod._prepare_policy_exports(
                {tier: self.root / "absent.pt" for tier in build_mod.TIER_NAMES},
                {tier: 0.0 for tier in build_mod.TIER_NAMES},
                policy_bundle_path=bundle_path,
            )
        self.assertEqual(len(report["policy_exports"]), 1)
        self.assertEqual(set(tiers), set(build_mod.TIER_NAMES))
        self.assertIn("GwaiNetTierConfig", sources)
        self.assertEqual(
            report["policy_exports"][0]["export_gate"]["node"]["agreement"], 200
        )


if __name__ == "__main__":
    unittest.main()
