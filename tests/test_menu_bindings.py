from __future__ import annotations

import json
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.test_dev_practice import javascript_body

ROOT = Path(__file__).resolve().parents[1]


class NativeMenuBindingTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("node"), "Node is required for the settings boundary"
    )
    def test_policy_difficulty_follows_native_fact_and_ignores_old_preferences(self):
        harness = r"""
const assert=require('node:assert/strict'), body=BODY;
const stale={GwaiDevTier:'2'};
let native=1;
const env={FactsQueryLatestValue(key){assert.equal(key,'gwent_difficulty');return native},
 theGame:{GetInGameConfigWrapper(){throw new Error('native difficulty must not consult mod preferences')}}};
const run=()=>new Function('env','with(env){'+body+'}')(env);
for(const [fact,expected] of [[0,1],[1,1],[2,2],[3,3],[4,3],[2,2],[1,1]]) {
 native=fact;assert.equal(run(),expected);assert.equal(stale.GwaiDevTier,'2');
}
""".replace("BODY", json.dumps(javascript_body("GwaiGwentDifficulty")))
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_menus_use_the_game_difficulty_without_an_extra_tier_control(
        self,
    ) -> None:
        for name in (
            "mod/ai/settings/menu/modW3GwentAI.xml",
            "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
        ):
            root = ET.parse(ROOT / name).getroot()
            self.assertIsNone(root.find(".//Var[@id='GwaiDevTier']"))
            self.assertIsNone(root.find(".//Var[@id='GwaiDifficulty']"))
            self.assertIsNone(root.find(".//Var[@id='GwaiDevAI']"))
            self.assertIsNone(root.find(".//Var[@id='GwaiClockEnabled']"))
            self.assertIsNone(root.find(".//Var[@id='GwaiClockScale']"))
            for setting, label in (
                ("GwaiEnabled", "Enable AI"),
                ("GwaiSignal", "Show decision source"),
                ("GwaiTrace", "Record public play-by-play"),
            ):
                self.assertEqual(
                    root.find(f".//Var[@id='{setting}']").get("displayName"), label
                )

    def test_core_controls_precede_optional_tools_and_troubleshooting(self) -> None:
        for name in (
            "mod/ai/settings/menu/modW3GwentAI.xml",
            "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
        ):
            root = ET.parse(ROOT / name).getroot()
            order = [var.get("id") for var in root.findall("Group/VisibleVars/Var")]
            self.assertEqual(order[0], "GwaiEnabled")
            self.assertLess(order.index("GwaiDevStart"), order.index("GwaiCoach"))
            self.assertLess(order.index("GwaiStats"), order.index("GwaiSignal"))
            self.assertLess(
                order.index("GwaiDiagnosticsHeader"), order.index("GwaiDevReport")
            )
            self.assertEqual(
                root.find(".//Var[@id='GwaiDiagnosticsHeader']").get("displayType"),
                "SEPARATOR",
            )

    def test_one_shot_actions_use_native_buttons_without_preference_values(
        self,
    ) -> None:
        for name in (
            "mod/ai/settings/menu/modW3GwentAI.xml",
            "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
        ):
            root = ET.parse(ROOT / name).getroot()
            for action in ("GwaiDevStart", "GwaiDevReport"):
                var = root.find(f".//Var[@id='{action}']")
                self.assertEqual(var.get("displayType"), "BUTTON")
                self.assertIsNone(var.find("OptionsArray"))

    @unittest.skipUnless(
        shutil.which("node"), "Node is required for the settings boundary check"
    )
    def test_initialization_ignores_removed_tier_preferences(
        self,
    ) -> None:
        bodies = {
            name: javascript_body(name)
            for name in ("GwaiSettingsInitialize", "GwaiSettingsNormalizeOff")
        }
        harness = r"""
const assert=require('node:assert/strict');const bodies=BODIES;
for(const raw of ['0','1','2','3','4','-1','','bad']) {
 const values={GwaiSettingsVersion:'3',GwaiEnabled:'false',GwaiSignal:'true',GwaiDevTier:raw,
 GwaiDevAI:'1',GwaiDevOpponent:'5',GwaiTrace:'true',GwaiStory:'true',GwaiCoach:'false'};
 const forbidden=new Set(['GwaiDevTier','GwaiDifficulty','GwaiClockEnabled','GwaiClockScale','GwaiDevAI']);
 const cfg={GetGroupIdx(){return -1},GetRawConfigValueByStr(g,key){assert.ok(!forbidden.has(key));return values[key]??''},
 SetRawConfigValueByStr(g,key,value){assert.ok(!forbidden.has(key));assert.ok(!['GwaiDevStart','GwaiDevReport'].includes(key),'button actions are not preferences');values[key]=value}};
 const env={theGame:{GetInGameConfigWrapper(){return cfg}},IntToString:String};
 env.GwaiSettingsNormalizeOff=(cfg,id)=>new Function('env','cfg','id','with(env){'+bodies.GwaiSettingsNormalizeOff+'}')(env,cfg,id);
 new Function('env','with(env){'+bodies.GwaiSettingsInitialize+'}')(env);
 assert.equal(values.GwaiDevTier,raw);
 assert.equal(values.GwaiEnabled,'false');assert.equal(values.GwaiTrace,'true');assert.equal(values.GwaiStory,'true');
}
""".replace("BODIES", json.dumps(bodies))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_custom_option_metadata_uses_native_zero_based_positions(self) -> None:
        for name in (
            "mod/ai/settings/menu/modW3GwentAI.xml",
            "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
        ):
            root = ET.parse(ROOT / name).getroot()
            for var in root.findall(".//Var[@displayType='OPTIONS']"):
                for index, option in enumerate(var.findall("OptionsArray/Option")):
                    self.assertEqual(option.get("id"), str(index), var.get("id"))
                    self.assertEqual(
                        option.find("Entry").get("value"), str(index), var.get("id")
                    )
