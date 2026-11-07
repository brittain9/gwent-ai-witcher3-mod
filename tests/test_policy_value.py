from __future__ import annotations

import unittest

from scripts.build_mod import ModBuildError, node_gate_failures, value_head_layout


class ValueHeadGateTests(unittest.TestCase):
    def summary(self, **changes: object) -> dict[str, object]:
        summary: dict[str, object] = {
            "total": 200,
            "agreement": 200,
            "decisionAgreement": 200,
            "decisionTotal": 200,
            "decisionFallbacks": 0,
            "easyDecisionTotal": 0,
            "easyDecisionFallbacks": 0,
            "easyDecisionInvalids": 0,
            "insightTotal": 200,
            "weightLoadCalls": 400,
            "logitsPass": True,
            "encoderPass": True,
            "valuePass": True,
            "valueTotal": 200,
        }
        summary.update(changes)
        return summary

    def test_build_gate_requires_the_value_head_and_counts_every_weight_load(
        self,
    ) -> None:
        self.assertEqual(node_gate_failures(self.summary()), [])
        self.assertIn(
            "value head and insight",
            node_gate_failures(self.summary(valuePass=False)),
        )
        self.assertIn(
            "value head and insight",
            node_gate_failures(self.summary(valueTotal=199)),
        )
        self.assertIn(
            "weight loads", node_gate_failures(self.summary(weightLoadCalls=600))
        )

    def test_export_without_a_full_value_head_is_refused(self) -> None:
        metadata = {
            "width": 240,
            "offsets": {
                "value_head.0.weight": 10,
                "value_head.0.bias": 20,
                "value_head.2.weight": 30,
                "value_head.2.bias": 40,
            },
            "shapes": {
                "value_head.0.weight": [128, 240],
                "value_head.0.bias": [128],
                "value_head.2.weight": [3, 128],
                "value_head.2.bias": [3],
            },
        }
        layout = value_head_layout(metadata)
        self.assertEqual(layout["value_head.2.bias"], {"offset": 40, "shape": [3]})
        del metadata["offsets"]["value_head.2.bias"]
        with self.assertRaisesRegex(ModBuildError, "value_head.2.bias"):
            value_head_layout(metadata)
