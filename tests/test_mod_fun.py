from __future__ import annotations

import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import mod_fun
from scripts.build_mod import (
    ModBuildError,
    _method_body,
    node_gate_failures,
    value_head_layout,
    verify_fun_helpers,
    verify_fun_sources,
)
from tests.test_mod_package import patched_mini_sources

ROOT = Path(__file__).resolve().parents[1]
AS3 = ROOT / "mod/ai/as3"
SETTINGS = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text(
    encoding="utf-8"
)
MENUS = {
    "ai": (ROOT / "mod/ai/settings/menu/modW3GwentAI.xml").read_text(encoding="utf-8"),
    "probe": (ROOT / "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml").read_text(
        encoding="utf-8"
    ),
}


class StoryTierTests(unittest.TestCase):
    def test_tournaments_and_quest_opponents_hard_random_players_normal(
        self,
    ) -> None:
        for name, category in mod_fun.DECK_CATEGORIES.items():
            with self.subTest(deck=name):
                if "Tournament" in name:
                    self.assertEqual(category, "tournament")
                    self.assertEqual(mod_fun.story_tier_for(name), 3)
                elif re.fullmatch(
                    r"(NK|Nilf|NML|Scoia|Skel)(Easy|Normal|Hard|Prologue)", name
                ):
                    self.assertEqual(category, "generic")
                    self.assertEqual(mod_fun.story_tier_for(name), 2)
                elif name in ("North", "Nilf", "scotial", "nml", "ske", "tutorialDeck"):
                    self.assertEqual(mod_fun.story_tier_for(name), 0)
                else:
                    self.assertEqual(category, "named")
                    self.assertEqual(mod_fun.story_tier_for(name), 3)
        self.assertEqual(mod_fun.story_tier_for(""), 0)
        self.assertEqual(mod_fun.story_tier_for("SomeModdedDeck"), 0)

    def test_generated_config_carries_every_tier_and_label(self) -> None:
        source = mod_fun.fun_config_source()
        self.assertTrue(source.startswith("internal class GwaiFunConfig"))
        tiers = source[
            source.index("STORY_TIERS:Object = {") : source.index(
                "};", source.index("STORY_TIERS")
            )
        ]
        for name in mod_fun.DECK_CATEGORIES:
            with self.subTest(deck=name):
                tier = mod_fun.story_tier_for(name)
                if tier:
                    self.assertIn(f'"{name}":{tier}', tiers)
                else:
                    self.assertNotIn(f'"{name}":', tiers)
                self.assertIn(
                    json.dumps(name) + ":" + json.dumps(mod_fun.deck_label(name)),
                    source,
                )
        self.assertIn(f"MAX_RECORDS:int = {mod_fun.MAX_RECORDS};", source)
        self.assertIn(f"BLUNDER_DROP:Number = {mod_fun.BLUNDER_DROP!r};", source)
        self.assertEqual(
            mod_fun.deck_label("CrossroadsInnkeeper"), "Crossroads Innkeeper"
        )
        self.assertEqual(
            mod_fun.deck_label("ScoiaNormal"), "a Scoia'tael player (normal)"
        )


class FeatureSettingsTests(unittest.TestCase):
    def test_every_feature_is_an_opt_in_toggle_in_both_menus_and_the_settings_script(
        self,
    ) -> None:
        manifest = mod_fun.manifest_section()
        self.assertFalse(manifest["default_enabled"])
        self.assertFalse(manifest["lab_mode"]["default_enabled"])
        lab = mod_fun.LAB_TOGGLE["toggle"]
        for edition, menu in MENUS.items():
            var = ET.fromstring(menu).find(f".//Var[@id='{lab}']")
            self.assertIsNotNone(var, edition)
            self.assertEqual(var.get("displayName"), mod_fun.LAB_TOGGLE["label"])
            self.assertEqual(var.get("displayType"), "TOGGLE")
        self.assertIn(f'GwaiSettingsNormalizeOff(cfg, "{lab}");', SETTINGS)
        self.assertIn(
            f'packet.SetMemberFlashBool("lab", GwaiSettingsToggleOn("{lab}"));',
            SETTINGS,
        )
        for feature, spec in mod_fun.FEATURES.items():
            toggle = spec["toggle"]
            flag = mod_fun.AS3_FLAGS[feature]
            with self.subTest(feature=feature):
                self.assertFalse(manifest["features"][feature]["default_enabled"])
                for edition, menu in MENUS.items():
                    var = ET.fromstring(menu).find(f".//Var[@id='{toggle}']")
                    self.assertIsNotNone(var, edition)
                    self.assertEqual(var.get("displayName"), spec["label"])
                    self.assertEqual(var.get("displayType"), "TOGGLE")
                    self.assertEqual(
                        "indent" in var.get("tags", "").split(";"),
                        spec["group"] == "lab",
                    )
                self.assertIn(f'GwaiSettingsNormalizeOff(cfg, "{toggle}");', SETTINGS)
                prefix = "!practice && " if feature == "story" else ""
                self.assertIn(
                    f'packet.SetMemberFlashBool("{flag}", {prefix}GwaiSettingsToggleOn("{toggle}"));',
                    SETTINGS,
                )

    def test_lab_mode_gates_exactly_the_analysis_tools(self) -> None:
        manifest = mod_fun.manifest_section()
        lab = [
            feature
            for feature, spec in mod_fun.FEATURES.items()
            if spec["group"] == "lab"
        ]
        self.assertEqual(lab, ["meter", "post_game", "blunder", "stats"])
        self.assertEqual(manifest["lab_mode"]["gates"], lab)
        for feature in lab:
            self.assertEqual(
                manifest["features"][feature]["requires"], "W3GwentAI.GwaiLab"
            )
        for feature in ("coach", "story"):
            self.assertNotIn("requires", manifest["features"][feature])
        # Lab tools on the WitcherScript side: stats counting and the deck read for stats.
        self.assertIn('if (!GwaiLabToggleOn("GwaiStats"))', SETTINGS)
        self.assertIn(
            'return GwaiSettingsToggleOn("GwaiLab") && GwaiSettingsToggleOn(id);',
            SETTINGS,
        )

    def test_missing_or_unknown_toggle_values_read_as_off(self) -> None:
        normalize = SETTINGS[SETTINGS.index("function GwaiSettingsNormalizeOff") :]
        normalize = normalize[: normalize.index("\n}\n")]
        self.assertIn('var normalized : string = "false";', normalize)
        self.assertIn('if (raw == "true" || raw == "1")', normalize)
        toggle = SETTINGS[SETTINGS.index("function GwaiSettingsToggleOn") :]
        toggle = toggle[: toggle.index("\n}\n")]
        self.assertIn('return raw == "true" || raw == "1";', toggle)

    def test_flash_events_and_stats_keys_match_the_settings_script(self) -> None:
        fun_source = (AS3 / "GwaiFun.as").read_text(encoding="utf-8")
        logic_source = (AS3 / "GwaiFunLogic.as").read_text(encoding="utf-8")
        events = set(re.findall(r'"(OnW3GwentFun\w+)"', fun_source))
        self.assertEqual(
            events,
            {
                "OnW3GwentFunStat",
                "OnW3GwentFunCard",
                "OnW3GwentFunNote",
                "OnW3GwentFunSave",
            },
        )
        for event in events:
            self.assertRegex(
                SETTINGS, rf"@addMethod\(CR4GwintGameMenu\) event {event}\("
            )
        script_buckets = re.findall(r'buckets\.PushBack\("(\w+)"\);', SETTINGS)
        script_outcomes = re.findall(r'outcomes\.PushBack\("(\w+)"\);', SETTINGS)
        self.assertEqual(tuple(script_buckets), mod_fun.STATS_BUCKETS)
        self.assertEqual(tuple(script_outcomes), mod_fun.STATS_OUTCOMES)
        as3_prefixes = re.search(
            r"STAT_PREFIXES:Array = \[([^\]]*)\]", logic_source
        ).group(1)
        as3_outcomes = re.search(r"OUTCOMES:Array = \[([^\]]*)\]", logic_source).group(
            1
        )
        self.assertEqual(
            tuple(json.loads(f"[{as3_prefixes}]")), mod_fun.STATS_BUCKETS + ("deck",)
        )
        self.assertEqual(tuple(json.loads(f"[{as3_outcomes}]")), mod_fun.STATS_OUTCOMES)

    def test_opponent_deck_is_read_only_when_practice_or_a_feature_needs_it(
        self,
    ) -> None:
        config_ui = SETTINGS[SETTINGS.index("@wrapMethod(CR4GwintGameMenu)") :]
        guard = config_ui.index(
            'if (practice || GwaiSettingsToggleOn("GwaiStory") || GwaiLabToggleOn("GwaiStats"))'
        )
        self.assertLess(
            guard, config_ui.index("deck = gwintManager.GetCurrentAIDeck();")
        )
        self.assertIn("deckName = NameToString(deck.deckName);", config_ui)


class FunHookTests(unittest.TestCase):
    def patched(self) -> dict[str, str]:
        return patched_mini_sources()

    def test_hooks_sit_at_their_planned_anchors_and_contain_errors(self) -> None:
        patched = self.patched()
        verify_fun_sources(
            patched["AIPlayerController"], patched["GwintGameFlowController"]
        )
        flow = patched["GwintGameFlowController"]
        final = "".join(_method_body(flow, "state_begin_ShowingFinalResult").split())
        self.assertLess(
            final.index("this.gwaiFunMatchEnd(gameWinner);"),
            final.index("this.mcEndGameDialog.show("),
        )
        turn = "".join(_method_body(flow, "state_update_PlayerTurn").split())
        self.assertTrue(turn.startswith("this.gwaiFunFrame();"))

    def test_the_ai_profile_takes_its_tier_from_the_shared_tested_rule(self) -> None:
        patched = self.patched()
        broken = patched["GwintGameFlowController"].replace(
            "return this.gwaiTier();", "return 99;", 1
        )
        with self.assertRaisesRegex(ModBuildError, "shared tier rule"):
            verify_fun_sources(patched["AIPlayerController"], broken)
        inline = patched["GwintGameFlowController"].replace(
            "return GwaiFunLogic.tierFor(", "return 0 + GwaiFunLogic.tierFor(", 1
        )
        with self.assertRaisesRegex(ModBuildError, "tested tier rule"):
            verify_fun_sources(patched["AIPlayerController"], inline)

    def test_a_flow_entry_point_without_its_own_catch_is_refused(self) -> None:
        patched = self.patched()
        flow = patched["GwintGameFlowController"]
        body = _method_body(flow, "gwaiFunTurnEnded")
        bare = body.replace("try", "", 1).replace(
            "catch(error:Error)\n         {\n            this._gwaiFun = null;\n         }",
            "",
            1,
        )
        with self.assertRaisesRegex(ModBuildError, "gwaiFunTurnEnded"):
            verify_fun_sources(
                patched["AIPlayerController"], flow.replace(body, bare, 1)
            )

    def test_a_statement_outside_an_entry_points_try_is_refused(self) -> None:
        patched = self.patched()
        flow = patched["GwintGameFlowController"]
        body = _method_body(flow, "gwaiFunFrame")
        outside = body.replace(
            "if(this._gwaiFun == null) return;",
            "if(this._gwaiFun == null) return;\n         this._gwaiFun.playerTurn(this,null,0);",
            1,
        )
        with self.assertRaisesRegex(ModBuildError, "gwaiFunFrame"):
            verify_fun_sources(
                patched["AIPlayerController"], flow.replace(body, outside, 1)
            )

    def test_the_meter_cannot_send_the_ai_to_its_fallback(self) -> None:
        patched = self.patched()
        ai = patched["AIPlayerController"]
        start = ai.index(
            "               try\n               {\n                  meter = "
        )
        end = ai.index('meter = "";\n               }', start) + len(
            'meter = "";\n               }'
        )
        bare = (
            ai[:start]
            + (
                "               meter = this.gameFlowControllerRef.gwaiMeterSuffix(Number(this._gwaiRunner.winChance));"
            )
            + ai[end:]
        )
        with self.assertRaisesRegex(ModBuildError, "meter suffix inside its own try"):
            verify_fun_sources(bare, patched["GwintGameFlowController"])


class FunContainmentTests(unittest.TestCase):
    """The lint refuses each form the review showed slipping past the first version."""

    def sources(self) -> tuple[dict[str, str], str]:
        helpers = {
            name: (AS3 / f"{name}.as").read_text(encoding="utf-8")
            for name in ("GwaiFun", "GwaiFunLogic")
        }
        return helpers, patched_mini_sources()["GwintGameFlowController"]

    def assert_refused(self, name: str, old: str, new: str, message: str) -> None:
        helpers, flow = self.sources()
        if name == "flow":
            self.assertEqual(flow.count(old), 1, old)
            flow = flow.replace(old, new, 1)
        else:
            self.assertEqual(helpers[name].count(old), 1, old)
            helpers[name] = helpers[name].replace(old, new, 1)
        with self.assertRaisesRegex(ModBuildError, message):
            verify_fun_helpers(helpers, flow)

    def test_the_shipped_feature_code_passes(self) -> None:
        verify_fun_helpers(*self.sources())

    def test_the_ai_seat_by_name(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "GwaiCapture.capture(manager,flow,CardManager.PLAYER_1)",
            "GwaiCapture.capture(manager,flow,CardManager.PLAYER_2)",
            "seat",
        )

    def test_a_literal_seat_index(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,CardManager.PLAYER_1)",
            "manager.getCardInstanceList(CardManager.CARD_LIST_LOC_HAND,1)",
            "seat",
        )

    def test_a_computed_seat(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "GwaiCapture.capture(manager,flow,CardManager.PLAYER_1)",
            "GwaiCapture.capture(manager,flow,1 - CardManager.PLAYER_1)",
            "seat",
        )

    def test_the_ai_seats_controller_in_the_flow(self) -> None:
        self.assert_refused(
            "flow",
            "this.playerControllers[CardManager.PLAYER_1];",
            "this.playerControllers[1 - CardManager.PLAYER_1];",
            "seat",
        )

    def test_sampling_through_a_string_key(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            'this._seen = Math["ran" + "dom"]();',
            "lookup",
        )

    def test_sampling_through_a_computed_class_member(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "var name:String = String(decisionId); Math[name]();",
            "lookup",
        )

    def test_sampling_in_the_pure_logic(self) -> None:
        self.assert_refused(
            "GwaiFunLogic",
            'if(isNaN(probability)) return "?";',
            "if(isNaN(probability)) return String(Math.random());",
            "sample",
        )

    def test_a_runner_that_calls_the_ais_decision(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "new GwaiRunner(view,moves,3,false,1,0).advance();",
            "decision path",
        )

    def test_reading_the_ais_own_win_chance(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "var winChance:Number = GwaiNetPolicy.winChanceOf(reading);",
            "var winChance:Number = GwaiNetPolicy.winChanceOf(reading) + 0 * GwaiNetPolicy.encodeObservation;",
            "GwaiNetPolicy.encodeObservation",
        )

    def test_the_card_manager_fetched_behind_the_flows_back(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "var hand:Vector.<CardInstance> = manager.getCardInstanceList(",
            "var hand:Vector.<CardInstance> = CardManager.getInstance().getCardInstanceList(",
            "allowlist: CardManager.getInstance",
        )

    # Second review pass: the members below take or index a seat; only the allowlist passes.
    def test_a_copy_of_the_ais_hand(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "manager.getCardInstanceListCopy(CardManager.CARD_LIST_LOC_HAND,1);",
            "allowlist: manager.getCardInstanceListCopy",
        )

    def test_the_ais_creatures_in_hand(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "manager.getAllCreaturesInHand(1);",
            "allowlist: manager.getAllCreaturesInHand",
        )

    def test_the_ais_hand_by_effect(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "manager.getCardsInHandWithEffect(0,1);",
            "allowlist: manager.getCardsInHandWithEffect",
        )

    def test_the_ais_deck_order(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "var order:Object = manager.playerDeckDefinitions[1].cardIndicesInDeck;",
            "allowlist: manager.playerDeckDefinitions",
        )

    def test_the_ais_renderer(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "var renderer:Object = manager.playerRenderers[1];",
            "allowlist: manager.playerRenderers",
        )

    def test_an_alias_of_the_player_controllers(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "var pcs:Object = flow.playerControllers; pcs[1];",
            "playerControllers",
        )

    def test_an_alias_of_the_card_manager(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "var m:Object = manager; m.cardValues;",
            "manager handed to a variable",
        )

    def test_the_card_manager_handed_to_another_class(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "GwaiFunLogic.percent(manager);",
            "manager handed to GwaiFunLogic.percent",
        )

    def test_an_alias_of_the_flow_in_its_own_methods(self) -> None:
        self.assert_refused(
            "flow",
            "if(this.gwaiFunInTutorial()) return;",
            "var self:Object = this; if(this.gwaiFunInTutorial()) return;",
            "the flow handed to a variable",
        )

    def test_a_flow_member_off_the_list(self) -> None:
        self.assert_refused(
            "flow",
            "if(this.gwaiFunInTutorial()) return;",
            "if(this.gwaiFunInTutorial() || this.mcChoiceDialog == null) return;",
            "allowlist: this.mcChoiceDialog",
        )

    def test_an_unknown_class(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            "var ai:Object = AIPlayerController.instance;",
            "allowlist: AIPlayerController.instance",
        )

    def test_a_class_looked_up_by_name(self) -> None:
        self.assert_refused(
            "GwaiFun",
            "this._seen = decisionId;",
            'flash.utils.getDefinitionByName("Gwai" + "Runner");',
            "getDefinitionByName",
        )

    def test_the_pure_logic_touching_the_game(self) -> None:
        self.assert_refused(
            "GwaiFunLogic",
            'if(isNaN(probability)) return "?";',
            "if(isNaN(probability)) return String(CardManager.PLAYER_1);",
            "pure",
        )


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


if __name__ == "__main__":
    unittest.main()
