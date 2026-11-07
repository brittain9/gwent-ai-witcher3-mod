from __future__ import annotations

import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.build_mod import (
    UNKNOWN_CARD_GUARD,
    ModBuildError,
    _difficulty_ladder,
    _method_body,
    patch_sources,
    verify_compiled_data_classes,
    verify_message_images,
    verify_opponent_hand_length_only,
    verify_unknown_card_hand_back,
)
from scripts.build_probe import ProbeBuildError

MINI_AI = """package red.game.witcher3.menus.gwint
{
   public class AIPlayerController
   {
      protected var _currentRoundCritical:Boolean = false;
      protected function state_begin_Idle() : void
      {
      }
      protected function state_begin_ChoseMove() : void
      {
         var hand:Vector.<CardInstance> = null;
         var numZerkers:int = 0;
         CardManager.getInstance().CalculateCardPowerPotentials();
         this.ChooseAttitude();
         this._decidedCardTransaction = this.decideWhichCardToPlay();
         if(this._decidedCardTransaction != null && this._decidedCardTransaction.sourceCardInstanceRef.templateRef.hasEffect(CardTemplate.CardEffect_Morph))
         {
            this.attitude = TACTIC_BESERKER;
         }
         else if(this._currentRoundCritical && this._decidedCardTransaction != null)
         {
            this.attitude = TACTIC_PASS;
         }
      }
      protected function state_update_ChooseMove() : void
      {
      }
   }
}
"""
MINI_FLOW = """package red.game.witcher3.menus.gwint
{
   public class GwintGameFlowController
   {
      public var gameStarted:Boolean = false;
      protected function state_update_CoinToss() : void
      {
         if(this.currentPlayer != CardManager.PLAYER_INVALID)
         {
         }
      }
      protected function state_update_PlayerTurn() : void
      {
         if(currentPlayerController.turnOver)
         {
         }
      }
      protected function state_begin_reset() : void
      {
      }
      protected function state_begin_ShowingFinalResult() : void
      {
         var gameWinner:int = CardManager.PLAYER_INVALID;
         this.mcEndGameDialog.show(gameWinner,this.OnEndGameResult);
      }
      public function EndGame(result:int) : void
      {
      }
   }
}
"""
MINI_MENU = """package red.game.witcher3.menus.gwint
{
   public class GwintGameMenu
   {
      public var gameFlowController:GwintGameFlowController;
      dispatchEvent(new GameEvent(GameEvent.REGISTER,"gwint.game.toggleAI",[this.setAIEnabled]));
   }
}
"""
MINI_HELPERS = {
    name: f"internal class {name} {{\n}}"
    for name in ("GwaiCapture", "GwaiPolicyCore", "GwaiRunner")
}


def patched_mini_sources() -> dict[str, str]:
    """The three hooked classes patched from minimal stand-ins of the game's sources."""
    return patch_sources(MINI_AI, MINI_FLOW, MINI_MENU, MINI_HELPERS)


class ModSourcePatchTests(unittest.TestCase):
    def test_method_extraction_distinguishes_a_name_from_a_longer_prefix_match(self):
        source = """
function GwaiMenuControls(storage : Object) : int
{
    return 6;
}
function GwaiMenuControl(storage : Object) : int
{
    return 17;
}
"""
        self.assertEqual(_method_body(source, "GwaiMenuControl").strip(), "return 17;")
        self.assertEqual(_method_body(source, "GwaiMenuControls").strip(), "return 6;")
        with self.assertRaisesRegex(ModBuildError, "missing method GwaiMenu"):
            _method_body(source, "GwaiMenu")

    def test_hooks_are_added_at_class_scope_and_settings_are_bridged(self) -> None:
        patched = patched_mini_sources()
        self.assertIn("if(this.gwaiBegin()) return;", patched["AIPlayerController"])
        self.assertIn("if(this.gwaiPump()) return;", patched["AIPlayerController"])
        self.assertIn("private var _gwaiRunner:Object", patched["AIPlayerController"])
        self.assertIn('"gwai.mod.settings"', patched["GwintGameMenu"])
        self.assertIn(
            "gwaiProfileForPlayer(side:int)", patched["GwintGameFlowController"]
        )
        self.assertIn(
            'gwaiNotifyDecision(profile:int, fallback:Boolean, detail:String = "")',
            patched["GwintGameFlowController"],
        )
        self.assertIn(
            'PushMessage("W3 Gwent AI: " + difficulty + " " + detail)',
            patched["GwintGameFlowController"],
        )
        self.assertIn(
            'PushMessage("W3 Gwent AI: fallback (vanilla AI plays this turn)")',
            patched["GwintGameFlowController"],
        )
        self.assertIn("this.gwaiCancelPending();", patched["GwintGameFlowController"])
        self.assertIn(
            "this._gwaiSettings.vanillaDifficulty", patched["GwintGameFlowController"]
        )
        self.assertIn(
            "this._gwaiMatchEnded = true;", patched["GwintGameFlowController"]
        )
        self.assertIn(
            "this.gameFlowControllerRef.gwaiMatchEnded()", patched["AIPlayerController"]
        )
        original_body = _method_body(
            patched["AIPlayerController"], "state_begin_ChoseMove"
        )
        original_body = original_body.replace(
            "\n         if(this.gwaiBegin()) return;", ""
        )
        self.assertEqual(
            "".join(original_body.split()),
            "".join(
                _method_body(
                    patched["AIPlayerController"], "gwaiNativeFallback"
                ).split()
            ),
        )
        self.assertLess(
            patched["AIPlayerController"].index("private var _gwaiRunner"),
            patched["AIPlayerController"].index("protected function state_begin_Idle"),
        )
        self.assertIn(
            "protected function state_begin_Idle() : void\n      {\n         this.gwaiCancel();",
            patched["AIPlayerController"],
        )

    def test_banners_use_only_vanilla_message_frame_labels(self) -> None:
        # The queue calls gotoAndStop(image) before it dequeues; an unknown label can stop the flow.
        verify_message_images(
            'q.PushMessage("[[gwint_round_start]]","round_start");', "vanilla"
        )
        verify_message_images(
            'q.PushMessage("W3 Gwent AI: " + a + ", " + b);', "no image"
        )
        verify_message_images(
            'q.PushMessage("x","north_ability",this.f,null);', "callbacks"
        )
        with self.assertRaisesRegex(ModBuildError, "gwai_network_decision"):
            verify_message_images(
                'q.PushMessage("W3 Gwent AI: " + detail,"gwai_network_decision");',
                "base",
            )
        with self.assertRaisesRegex(ModBuildError, "image"):
            verify_message_images('q.PushMessage("x",imageName);', "computed")

    def test_hook_patching_rejects_an_ambiguous_ai_anchor(self) -> None:
        with self.assertRaises(ProbeBuildError):
            patch_sources("missing", "", "", {})

    def test_capture_source_allows_only_opponent_hand_length(self) -> None:
        capture = Path(__file__).resolve().parents[1] / "mod/ai/as3/GwaiCapture.as"
        source = capture.read_text(encoding="utf-8")
        verify_opponent_hand_length_only(source)
        with self.assertRaises(ModBuildError):
            verify_opponent_hand_length_only(
                source.replace(
                    "var opponentHandCount:int = opponentHand.length;",
                    "var opponentHandCount:int = opponentHand[0].templateId;",
                )
            )

    def test_exported_capture_source_guard_checks_opponent_hand_length(self) -> None:
        exported = """package red.game.witcher3.menus.gwint
{
   internal class GwaiCapture
   {
      public static function capture(manager:CardManager, flow:GwintGameFlowController, playerID:int) : Object
      {
         var opponentID:int = playerID == CardManager.PLAYER_1 ? CardManager.PLAYER_2 : CardManager.PLAYER_1;
         var opponentHand:Vector.<CardInstance> = manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,opponentID);
         return {opponentHandCount:opponentHand.length};
      }
   }
}"""
        verify_opponent_hand_length_only(exported)
        with self.assertRaises(ModBuildError):
            verify_opponent_hand_length_only(
                exported.replace("opponentHand.length", "opponentHand[0]")
            )

    def test_compiled_checkpoint_data_matches_requested_hashes_and_rejects_extras(
        self,
    ) -> None:
        name = "GwaiNetWeightsData_0123456789abcdef"
        checkpoint_hash = "a" * 64
        compiled = {
            name: f'internal class {name} {{ public static const CHECKPOINT_SHA256:String = "{checkpoint_hash}"; }}'
        }

        verify_compiled_data_classes(compiled, {name: checkpoint_hash})

        with self.assertRaisesRegex(ModBuildError, "expected"):
            verify_compiled_data_classes(
                {name: compiled[name].replace(checkpoint_hash, "b" * 64)},
                {name: checkpoint_hash},
            )
        with self.assertRaisesRegex(ModBuildError, "extra"):
            verify_compiled_data_classes(
                {
                    **compiled,
                    "GwaiNetWeightsData_extra": "internal class GwaiNetWeightsData_extra {}",
                },
                {name: checkpoint_hash},
            )

    def test_settings_use_vanilla_event_declarations_default_signal_off_and_match_suite_difficulties(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        settings = (root / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text(
            encoding="utf-8"
        )
        menu = (root / "mod/ai/settings/menu/modW3GwentAI.xml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(settings.count("function OnConfigUI()"), 2)
        self.assertNotIn("event OnConfigUI", settings)
        self.assertIn('var normalizedEnabled : string = "true";', settings)
        self.assertIn('if (enabled == "false" || enabled == "0")', settings)
        self.assertIn('var normalizedSignal : string = "false";', settings)
        self.assertNotIn("GwaiNetworkPolicy", settings)
        self.assertNotIn("GwaiNetworkPolicy", menu)
        self.assertIn(
            'var settingsVersion : string = cfg.GetRawConfigValueByStr("W3GwentAI", "GwaiSettingsVersion");',
            settings,
        )
        self.assertIn('if (settingsVersion == "" || settingsVersion == "1")', settings)
        self.assertIn(
            'cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiEnabled", "true");',
            settings,
        )
        self.assertIn(
            'cfg.SetRawConfigValueByStr("W3GwentAI", "GwaiSettingsVersion", "3");',
            settings,
        )
        self.assertIn('displayName="Enable AI"', menu)
        self.assertIn("FactsQueryLatestValue('gwent_difficulty')", settings)

    def test_manifest_ladder_links_three_policies_to_game_difficulty(self):
        tiers = {
            name: {"checkpoint_sha256": str(index) * 64, "temperature": 0.1 * index}
            for index, name in enumerate(("easy", "normal", "hard"), start=1)
        }
        ladder = _difficulty_ladder(tiers)
        self.assertEqual(ladder["vanilla_difficulty_fact"], "gwent_difficulty")
        self.assertEqual(set(ladder["tiers"]), set(tiers))
        for index, (name, policy) in enumerate(tiers.items(), start=1):
            with self.subTest(tier=name):
                self.assertEqual(
                    ladder["tiers"][name]["game_difficulty"]["fact_value"], index
                )
                self.assertEqual(
                    ladder["tiers"][name]["game_difficulty"]["label"],
                    ("Easy", "Medium", "Hard")[index - 1],
                )
                self.assertEqual(
                    ladder["tiers"][name]["checkpoint_sha256"],
                    policy["checkpoint_sha256"],
                )
                self.assertEqual(
                    ladder["tiers"][name]["temperature"], policy["temperature"]
                )

    def test_an_unknown_card_hands_the_turn_to_the_vanilla_ai_before_the_runner(
        self,
    ) -> None:
        patched = patched_mini_sources()
        ai = patched["AIPlayerController"]
        verify_unknown_card_hand_back(ai)
        begin = "".join(_method_body(ai, "gwaiBegin").split())
        hand_back = begin.index("if(unknownCards.length>0)")
        self.assertLess(begin.index("GwaiCapture.capture("), hand_back)
        self.assertLess(hand_back, begin.index("GwaiCapture.legalMoves("))
        self.assertLess(hand_back, begin.index("newGwaiRunner("))
        # A begin hook that returns false lets the vanilla state_begin_ChoseMove body decide.
        self.assertTrue(
            begin[hand_back:].startswith(
                'if(unknownCards.length>0){this.gwaiReportDecision(profile,true,"unknowncardvocabulary");'
                "this.gwaiCancel();returnfalse;}"
            )
        )
        notify = _method_body(patched["GwintGameFlowController"], "gwaiNotifyDecision")
        self.assertIn(
            'PushMessage("W3 Gwent AI: " + detail + ", vanilla AI plays this turn")',
            notify,
        )
        self.assertIn(
            'PushMessage("W3 Gwent AI: fallback (vanilla AI plays this turn)")', notify
        )
        for broken in (
            ai.replace("if(unknownCards.length > 0)", "if(false)"),
            ai.replace(UNKNOWN_CARD_GUARD, "[]"),
        ):
            with self.assertRaises(ModBuildError):
                verify_unknown_card_hand_back(broken)


class RecorderImportTests(unittest.TestCase):
    def test_recorder_build_module_import_does_not_require_torch(self) -> None:
        code = r"""import importlib.abc, sys
class BlockTorch(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "torch" or fullname.startswith("torch."):
            raise ImportError("torch blocked for recorder import check")
sys.meta_path.insert(0, BlockTorch())
import scripts.build_mod
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()


class WitcherScriptDeclarationTests(unittest.TestCase):
    def test_menu_path_uses_native_titles_and_literal_labels_skip_localization(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "mod/ai/settings/menu/modW3GwentAI.xml",
            "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
        ):
            group = ET.parse(root / relative).getroot().find("Group")
            self.assertIsNotNone(group)
            # Native menus use panel_mainmenu_mods / panel_mainmenu_gwent.
            # A literal English group name resolves to an empty localization.
            self.assertEqual(group.get("displayName"), "mainmenu_mods.mainmenu_gwent")
            for var in group.findall("VisibleVars/Var"):
                tags = set(var.get("tags", "").split(";"))
                self.assertIn("nonLocalizedName", tags, var.get("id"))
                if var.get("displayType") == "OPTIONS":
                    self.assertTrue(
                        {"customNames", "nonLocalized"} <= tags, var.get("id")
                    )

    def test_boolean_events_do_not_return_void(self) -> None:
        # WitcherScript events implicitly return Bool. A function wrapping an
        # event inherits that signature even when its declaration omits it.
        # The actual compiler rejected bare returns in these callbacks.
        event_wrappers = {"OnConfigUI", "OnClosingMenu", "OnButtonClicked"}
        root = Path(__file__).resolve().parents[1] / "mod"
        for script in root.rglob("*.ws"):
            source = script.read_text(encoding="utf-8")
            declarations = list(
                re.finditer(
                    r"\b(event|function)\s+(\w+)\s*\([^)]*\)(?:\s*:\s*\w+)?\s*\{",
                    source,
                )
            )
            for match in declarations:
                kind, name = match.group(1, 2)
                if kind != "event" and name not in event_wrappers:
                    continue
                # Slice from this declaration so repeated method names in
                # different classes are checked independently.
                declaration = source[match.start() :]
                if kind == "event":
                    declaration = declaration.replace("event", "function", 1)
                body = _method_body(declaration, name)
                self.assertNotRegex(
                    body, r"\breturn\s*;", f"{script.name}: {name} returns Bool"
                )

    def test_local_variables_are_declared_before_any_statement(self) -> None:
        # WitcherScript locals belong at the top of a function body; a late `var` risks
        # a compile error that disables the whole script mod in the game.
        root = Path(__file__).resolve().parents[1] / "mod"
        scripts = sorted(root.rglob("*.ws"))
        self.assertTrue(scripts)
        for script in scripts:
            in_body = False
            seen_statement = False
            depth = 0
            for number, line in enumerate(
                script.read_text(encoding="utf-8").splitlines(), 1
            ):
                text = line.strip()
                if not in_body:
                    if text == "{":
                        in_body, seen_statement, depth = True, False, 1
                    continue
                depth += line.count("{") - line.count("}")
                if depth <= 0:
                    in_body = False
                    continue
                if not text or text.startswith("//") or text in ("{", "}"):
                    continue
                if text.startswith("var "):
                    self.assertFalse(
                        seen_statement, f"{script.name}:{number}: var after a statement"
                    )
                else:
                    seen_statement = True
