"""Shared screen hooks keep native transaction authority and one model identity."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_shared_player_bundle import (
    policy_only_controller,
    synthetic_catalog,
    synthetic_export,
)


def native_stubs() -> dict[str, str]:
    # Authored synthetic syntax fixtures, not decompiled game source.
    return {
        "AIPlayerController": """package red.game.witcher3.menus.gwint
{
   public class AIPlayerController
   {
      protected var _currentRoundCritical:Boolean = false;
      protected function state_begin_ChoseMove() : void
      {
         nativeChoose();
      }
      protected function state_update_ChooseMove() : void
      {
         nativeUpdate();
      }
      protected function state_begin_Idle() : void
      {
         nativeIdle();
      }
   }
}
""",
        "GwintGameFlowController": """package red.game.witcher3.menus.gwint
{
   public class GwintGameFlowController
   {
      public var gameStarted:Boolean = false;
      protected function state_begin_reset() : void
      {
         nativeReset();
      }
      public function EndGame(result:int) : void
      {
         nativeEnd();
      }
      protected function state_update_CoinToss() : void
      {
         if(this.currentPlayer != CardManager.PLAYER_INVALID)
         {
            nativeCoin();
         }
      }
      protected function state_update_PlayerTurn() : void
      {
         if(currentPlayerController.turnOver)
         {
            nativeEndTurn();
         }
      }
   }
}
""",
        "GwintGameMenu": """package red.game.witcher3.menus.gwint
{
   public class GwintGameMenu
   {
      public var gameFlowController:GwintGameFlowController;
      public function configure() : void
      {
         dispatchEvent(new GameEvent(GameEvent.REGISTER,"gwint.game.toggleAI",[this.setAIEnabled]));
      }
   }
}
""",
    }


class SharedHostTests(unittest.TestCase):
    def helpers(self, root: Path) -> dict[str, str]:
        import json

        from shared_player.bundle import create_bundle, materialize
        from shared_player.sources import AS3_ROOT, build_sources

        exported = synthetic_export(root / "export")
        create_bundle(exported, root / "bundle", controller=policy_only_controller())
        catalog = root / "catalog.json"
        catalog.write_text(json.dumps(synthetic_catalog()), encoding="utf-8")
        prepared = materialize(root / "bundle", catalog, root / "prepared")
        helpers = build_sources(prepared)
        helpers["GwaiCapture"] = (AS3_ROOT / "GwaiCapture.as").read_text(
            encoding="utf-8"
        )
        return helpers

    def test_shared_hooks_disable_before_capture_and_preserve_native_choice(self):
        from shared_player.host import patch_sources

        with tempfile.TemporaryDirectory() as directory:
            helpers = self.helpers(Path(directory))
            patched = patch_sources(native_stubs(), helpers)
            ai = patched["AIPlayerController"]
            self.assertLess(
                ai.index("GwaiSharedConfig.enabled()"), ai.index("GwaiCapture.capture")
            )
            self.assertIn("new GwaiSharedController(profile)", ai)
            self.assertIn(".begin(this._gwaiView.observation,this._gwaiMoves)", ai)
            self.assertIn("nativeChoose();", ai)
            self.assertIn("_gwaiCapturedDecisionId", ai)
            self.assertIn("getQualifiedClassName", ai)
            for obsolete in (
                "GwaiNetTierConfig",
                "GwaiPolicyCore",
                "new GwaiRunner",
                "greedy-fallback",
            ):
                self.assertNotIn(obsolete, "\n".join(patched.values()))
            # Actual accepted-chain/pass counting is checked with the complete
            # native assembly, rather than treating a flow poll as a completion.
            self.assertEqual(ai.count("internal class GwaiNetWeightsData_"), 1)

    def test_second_patch_and_missing_native_anchor_are_refused(self):
        from shared_player.host import HostIntegrationError, patch_sources

        with tempfile.TemporaryDirectory() as directory:
            helpers = self.helpers(Path(directory))
            originals = native_stubs()
            patched = patch_sources(originals, helpers)
            with self.assertRaises(HostIntegrationError):
                patch_sources(patched, helpers)
            originals["AIPlayerController"] = originals["AIPlayerController"].replace(
                "state_begin_ChoseMove", "unknownNativeEntry"
            )
            with self.assertRaises(HostIntegrationError):
                patch_sources(originals, helpers)

    def test_three_model_helper_inventory_is_refused(self):
        from shared_player.host import HostIntegrationError, patch_sources

        with tempfile.TemporaryDirectory() as directory:
            helpers = self.helpers(Path(directory))
            helpers["GwaiNetTierConfig"] = "internal class GwaiNetTierConfig {}"
            with self.assertRaisesRegex(
                HostIntegrationError, "inventory|shared|helper"
            ):
                patch_sources(native_stubs(), helpers)


if __name__ == "__main__":
    unittest.main()
