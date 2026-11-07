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
        shutil.which("node"), "Node is required for the settings boundary check"
    )
    def test_selected_native_list_position_decodes_to_the_displayed_choice(
        self,
    ) -> None:
        # igmOptions sends raw current values plus an array of display strings.
        # OnOptionValueChanged stores the selected zero-based position, not an
        # arbitrary Option.id or Entry.value. Replay that actual UI boundary.
        cases = (("GwaiDevTier", "GwaiPracticeTier", [1, 2, 3]),)
        payload = [
            {"setting": key, "body": javascript_body(method), "expected": values}
            for key, method, values in cases
        ]
        harness = r"""
const assert = require('node:assert/strict');
for (const c of CASES) {
    for (let selectedIndex = 0; selectedIndex < c.expected.length; selectedIndex++) {
        const env = {
            GwaiSettingsEnabled() { return true; },
            theGame: { GetInGameConfigWrapper() { return {
                GetRawConfigValueByStr(group, key) { return key === c.setting ? String(selectedIndex) : ''; }
            }; } },
            StringToInt(s, fallback) { const n = Number.parseInt(s, 10); return Number.isNaN(n) ? fallback : n; }
        };
        const actual = new Function('env', 'with(env){' + c.body + '}')(env);
        assert.equal(actual, c.expected[selectedIndex], c.setting + ' visible choice ' + selectedIndex);
    }
}
""".replace("CASES", json.dumps(payload))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(
        shutil.which("node"), "Node is required for the settings boundary check"
    )
    def test_practice_tier_accepts_only_the_three_displayed_choices(self) -> None:
        harness = r"""
const assert=require('node:assert/strict');const body=BODY;
for(const [raw,expected] of [['0',1],['1',2],['2',3],['3',3],['4',3],['-1',3],['',3],['garbage',3],['1extra',3],['2.5',3]]) {
 const env={theGame:{GetInGameConfigWrapper(){return {GetRawConfigValueByStr(){return raw}}}},
 StringToInt(s,fallback){const n=Number.parseInt(s,10);return Number.isNaN(n)?fallback:n}};
 assert.equal(new Function('env','with(env){'+body+'}')(env),expected,raw);
}
""".replace("BODY", json.dumps(javascript_body("GwaiPracticeTier")))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_menus_expose_exactly_easy_normal_hard_and_no_story_override(
        self,
    ) -> None:
        for name in (
            "mod/ai/settings/menu/modW3GwentAI.xml",
            "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
        ):
            root = ET.parse(ROOT / name).getroot()
            tier = root.find(".//Var[@id='GwaiDevTier']")
            self.assertEqual(
                [
                    option.get("displayName")
                    for option in tier.findall("OptionsArray/Option")
                ],
                ["Easy", "Normal", "Hard"],
            )
            self.assertIsNone(root.find(".//Var[@id='GwaiDifficulty']"))
            self.assertIsNone(root.find(".//Var[@id='GwaiDevAI']"))
            self.assertIsNone(root.find(".//Var[@id='GwaiClockEnabled']"))
            self.assertIsNone(root.find(".//Var[@id='GwaiClockScale']"))
            for setting, label in (
                ("GwaiEnabled", "Enable trained AI"),
                ("GwaiSignal", "[Debug] Show decision source"),
                ("GwaiTrace", "[Debug] Record play-by-play"),
            ):
                self.assertEqual(
                    root.find(f".//Var[@id='{setting}']").get("displayName"), label
                )

    @unittest.skipUnless(
        shutil.which("node"), "Node is required for the settings boundary check"
    )
    def test_initialization_preserves_valid_tiers_and_ignores_removed_preferences(
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
 const forbidden=new Set(['GwaiDifficulty','GwaiClockEnabled','GwaiClockScale','GwaiDevAI']);
 const cfg={GetGroupIdx(){return -1},GetRawConfigValueByStr(g,key){assert.ok(!forbidden.has(key));return values[key]??''},
 SetRawConfigValueByStr(g,key,value){assert.ok(!forbidden.has(key));values[key]=value}};
 const env={theGame:{GetInGameConfigWrapper(){return cfg}},IntToString:String};
 env.GwaiSettingsNormalizeOff=(cfg,id)=>new Function('env','cfg','id','with(env){'+bodies.GwaiSettingsNormalizeOff+'}')(env,cfg,id);
 new Function('env','with(env){'+bodies.GwaiSettingsInitialize+'}')(env);
 assert.equal(values.GwaiDevTier,['0','1','2'].includes(raw)?raw:'2');
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
