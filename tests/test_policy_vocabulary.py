"""The network's card vocabulary is the catalog its checkpoint was trained on, and the mod's
unknown-card guard reads exactly the vocabulary the exporter writes."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import torch

from learning.catalog_identity import catalog_identity
from scripts import export_policy_as3

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests.synthetic_policy import (  # noqa: E402
    CARD_IDS,
    decision,
    exportable_checkpoint,
    synthetic_suite,
)

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
EXPANSION_CARD = 9003

# Loads the exported network the way the build's Node gate does and reads one decision per case.
GUARD_PROBE = """
const fs = require("node:fs");
const { loadNetworkPolicy } = require(process.argv[2]);
const [network, core, metadataPath, weightsPath, casesPath] = process.argv.slice(3);
const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
const bytes = fs.readFileSync(weightsPath);
const weights = Array.from({ length: metadata.parameter_count }, (_, index) => bytes.readFloatLE(index * 4));
const policy = loadNetworkPolicy(fs.readFileSync(network, "utf8"), fs.readFileSync(core, "utf8"), metadata, weights);
const results = JSON.parse(fs.readFileSync(casesPath, "utf8")).map(({ observation, legal }) => {
  const view = { policyMode: "network", observation, playerID: 0, opponentID: 1, scores: [0, 0], opponentPassed: false };
  const move = policy.decide(view, legal, 2);
  return {
    unknown: [...policy.unknownCards(observation, metadata.card_ids)],
    fallback: view.policyFallback === true,
    reason: String(view.policyFallbackReason || ""),
    move: policy.actionId(move),
  };
});
process.stdout.write(JSON.stringify(results));
"""


class ExportedVocabularyTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        _, self.catalog = synthetic_suite()
        self.catalog_path = self.root / "catalog.json"
        self.catalog_path.write_text(json.dumps(self.catalog), encoding="utf-8")
        self.checkpoint = exportable_checkpoint(
            self.root / "policy.pt", self.catalog_path, seed=3
        )

    def test_exported_vocabulary_is_the_catalog_the_checkpoint_was_trained_on(
        self,
    ) -> None:
        metadata = export_policy_as3.export(
            self.checkpoint, self.catalog_path, self.root / "export"
        )
        catalog_ids = sorted(int(card["id"]) for card in self.catalog["cards"])
        self.assertEqual(catalog_ids, CARD_IDS)
        self.assertEqual(metadata["card_ids"], catalog_ids)
        self.assertEqual(len(metadata["card_effects"]), len(catalog_ids))
        header = (self.root / "export" / "GwaiNetWeightsData.as").read_text(
            encoding="utf-8"
        )
        exported = re.search(
            r"public static const CARD_IDS:Array = \[([^\]]*)\];", header
        )
        self.assertIsNotNone(exported)
        self.assertEqual(
            [int(value) for value in exported.group(1).split(",")], catalog_ids
        )

    def test_a_catalog_card_the_network_never_saw_is_refused(self) -> None:
        # A catalog re-extracted after an expansion: one more card than the checkpoint's vocabulary.
        grown = copy.deepcopy(self.catalog)
        grown["cards"].append({**grown["cards"][-1], "id": EXPANSION_CARD})
        grown_path = self.root / "grown-catalog.json"
        grown_path.write_text(json.dumps(grown), encoding="utf-8")
        checkpoint = torch.load(self.checkpoint, map_location="cpu", weights_only=False)
        checkpoint["identity"]["catalog_sha256"] = catalog_identity(grown)
        relabelled = self.root / "relabelled.pt"
        torch.save(checkpoint, relabelled)
        with self.assertRaisesRegex(ValueError, "card vocabulary does not match"):
            export_policy_as3.export(relabelled, grown_path, self.root / "grown-export")
        with self.assertRaisesRegex(ValueError, "catalog hash does not match"):
            export_policy_as3.export(
                self.checkpoint, grown_path, self.root / "grown-export"
            )

    @unittest.skipUnless(NODE, "Node.js is required to run the AS3 policy source")
    def test_the_in_game_guard_names_cards_outside_the_exported_vocabulary(
        self,
    ) -> None:
        export_dir = self.root / "export"
        export_policy_as3.export(self.checkpoint, self.catalog_path, export_dir)
        observation, legal = decision()
        expansion = copy.deepcopy(observation)
        expansion["players"][1]["graveyard"].append(
            {
                "instance_id": 40,
                "template_id": EXPANSION_CARD,
                "power": 6,
                "score": 6,
                "effects": [],
                "owner": 1,
            }
        )
        cases = self.root / "cases.json"
        cases.write_text(
            json.dumps(
                [
                    {"observation": observation, "legal": legal},
                    {"observation": expansion, "legal": legal},
                ]
            ),
            encoding="utf-8",
        )
        probe = self.root / "guard-probe.js"
        probe.write_text(GUARD_PROBE, encoding="utf-8")
        result = subprocess.run(
            [
                NODE,
                str(probe),
                str(ROOT / "mod/ai/node/replay.js"),
                str(ROOT / "mod/ai/as3/GwaiNetPolicy.as"),
                str(ROOT / "mod/ai/as3/GwaiPolicyCore.as"),
                str(export_dir / "metadata.json"),
                str(export_dir / "policy.f32.bin"),
                str(cases),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        known, unknown = json.loads(result.stdout)
        self.assertEqual(known["unknown"], [])
        self.assertFalse(known["fallback"], known["reason"])
        self.assertIn(known["move"], {action["id"] for action in legal})
        # gwaiBegin hands this turn to the vanilla AI; past that guard the network still refuses it.
        self.assertEqual(unknown["unknown"], [EXPANSION_CARD])
        self.assertTrue(unknown["fallback"])
        self.assertIn("outside the frozen catalog vocabulary", unknown["reason"])


if __name__ == "__main__":
    unittest.main()
