"""The real host injects owned draws once per admitted primary ticket."""

import unittest
from pathlib import Path

from shared_player.host import _body
from shared_player.host_hooks import AI_MEMBERS, FLOW_METHODS
from shared_player.owned_actions import (
    OwnedActionError,
    patch_owned_actions,
    verify_owned_actions,
)

ROOT = Path(__file__).resolve().parents[1]


def sources():
    flow = """package red.game.witcher3.menus.gwint
{
 public class GwintGameFlowController {
 public var gameStarted:Boolean = false;
 protected function state_begin_SpawnLeaders() : void { authoredSpawn(); }
 }
}
"""
    flow = flow.replace(
        "public var gameStarted:Boolean = false;",
        "public var gameStarted:Boolean = false;\n" + FLOW_METHODS,
    )
    return {"AIPlayerController": AI_MEMBERS, "GwintGameFlowController": flow}


class OwnedActionHostTests(unittest.TestCase):
    def test_compiled_gate_requires_actual_uniform_injection_and_hard_zero_boundary(
        self,
    ):
        patched = patch_owned_actions(sources())
        self.assertFalse(verify_owned_actions(patched)["runtime_execution_verified"])
        patched["AIPlayerController"] = patched["AIPlayerController"].replace(
            ".gwaiOwnedActionUniforms(", ".otherUniforms("
        )
        with self.assertRaises(OwnedActionError):
            verify_owned_actions(patched)

    def test_native_constructor_gets_ticket_bound_uniforms_and_match_seed(self):
        patched = patch_owned_actions(sources())
        ai = patched["AIPlayerController"]
        self.assertIn(
            "new GwaiSharedController(profile,this.gameFlowControllerRef.gwaiOwnedActionUniforms(",
            ai,
        )
        self.assertIn("gwaiPrimaryTicket(this.playerID)", ai)
        flow = patched["GwintGameFlowController"]
        self.assertIn("this.gwaiOwnedActionReset(getTimer());", flow)
        self.assertIn("import flash.utils.getTimer;", flow)
        for method in (
            "gwaiOwnedActionReset",
            "gwaiOwnedActionUniforms",
            "gwaiOwnedActionState",
        ):
            start, end = _body(flow, method)
            self.assertNotIn("Math.random", flow[start:end])
        selector = (ROOT / "mod/ai/as3/GwaiNetPolicy.as").read_text()
        for method in ("ownedActionUniforms", "sampleLogit", "temperatureWeights"):
            start, end = _body(selector, method)
            self.assertNotIn("Math.random", selector[start:end])

    def test_missing_native_hook_refuses_without_partial_output(self):
        original = sources()
        original["AIPlayerController"] = original["AIPlayerController"].replace(
            "new GwaiSharedController(profile)", "new OtherController(profile)"
        )
        with self.assertRaises(OwnedActionError):
            patch_owned_actions(original)

    def test_double_patch_refuses_and_original_inputs_are_unchanged(self):
        original = sources()
        before = dict(original)
        patched = patch_owned_actions(original)
        self.assertEqual(original, before)
        with self.assertRaises(OwnedActionError):
            patch_owned_actions(patched)
