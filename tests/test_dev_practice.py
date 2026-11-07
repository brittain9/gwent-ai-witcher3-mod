from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.build_mod import _method_body

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text()


def javascript_body(method: str, source: str = SOURCE) -> str:
    # Execute the control flow against a fake game boundary. This is not a
    # WitcherScript compiler: the live game still has to compile the script.
    body = _method_body(source, method)
    body = re.sub(r"var (\w+)\s*:\s*[\w<>]+(?=\s*[=;])", r"var \1", body)
    body = body.replace("(CR4CommonIngameMenu)GetRootMenu()", "GetRootMenu()")
    body = body.replace("var extraCards;", "var extraCards = [];")
    return body


@unittest.skipUnless(
    shutil.which("node"), "Node is required for the script boundary harness"
)
class DevPracticeTests(unittest.TestCase):
    def test_launch_guards_and_state_restoration(self) -> None:
        bodies = {
            name: javascript_body(name)
            for name in (
                "OnButtonClicked",
                "GwaiDevBegin",
                "GwaiDevRestore",
                "GwaiPracticeAI",
                "GwaiSettingsEnabled",
                "GwaiGwentDifficulty",
                "GwaiPracticeOpponent",
                "GwaiPracticeOpponentLabel",
            )
        }
        configure = SOURCE[
            SOURCE.index("@wrapMethod(CR4GwintGameMenu)\nfunction OnConfigUI") :
        ]
        bodies["configure"] = javascript_body("OnConfigUI", configure)
        bodies["matchClose"] = javascript_body("OnClosingMenu")
        deck_source = SOURCE[SOURCE.index("@wrapMethod(CR4DeckBuilderMenu)") :]
        bodies["deckClose"] = javascript_body("OnClosingMenu", deck_source)
        harness = r"""
const assert = require('node:assert/strict');
const bodies = BODIES;
function run(name, env) { return new Function('env', 'with(env){' + bodies[name] + '}')(env); }
function fixture(overrides = {}) {
    const events = [], dialogs = [], values = { GwaiDevOpponent: '0', GwaiDevTier: '2', GwaiEnabled: 'true' };
    const m = { gameRequested: false, testMatch: false, gwaiDevActive: false,
        gwaiDevOldExtra: [], selectedEnemyDeck: 'Thaler', tutorial: false,
        deckTutorial: false, doubleAI: true, faction: 3, extra: ['bonus'], gwaiDevAI: 0, gwaiDevLabel: '',
        GetCurrentAIDeck() { return { deckName: 'Thaler' }; },
        GetHasDoneTutorial() { return this.tutorial; },
        SetHasDoneTutorial(v) { this.tutorial = v; },
        GetHasDoneDeckTutorial() { return this.deckTutorial; },
        SetHasDoneDeckTutorial(v) { this.deckTutorial = v; },
        getDoubleAIEnabled() { return this.doubleAI; },
        setDoubleAIEnabled(v) { this.doubleAI = v; },
        GetForcedFaction() { return this.faction; },
        SetForcedFaction(v) { this.faction = v; },
        GetAdditionalCards() { return this.extra.slice(); },
        SetAdditionalCards(v) { this.extra = v.slice(); },
        SetEnemyDeck(v) { this.selectedEnemyDeck = v; },
    };
    // Model WitcherScript's value-array assignment and Clear, not JS aliasing.
    Object.defineProperty(m, 'gwaiDevOldExtra', { configurable: true,
        get() { return this.savedExtra; },
        set(v) { this.savedExtra = v.slice(); this.savedExtra.Clear = function() { this.length = 0; }; }
    });
    const cfg = { GetGroupName() { throw new Error('synthetic UI must not resolve native group'); },
        GetRawConfigValueByStr(g, k) { assert.ok(!['GwaiDevTier','GwaiDevAI','GwaiDevStart','GwaiDevReport'].includes(k),'removed preferences must never be read');return values[k] || ''; },
        SetRawConfigValueByStr(g, k, v) { assert.ok(!['GwaiDevStart','GwaiDevReport'].includes(k),'buttons do not write preferences');values[k] = v; } };
    const env = { nativeDifficulty:2, GwaiMenuGroupId:()=>31415, groupId: 31415, optionName: 'GwaiDevStart', UDB_Ok:'OK',
        StringToInt: (s, d) => { const n = Number.parseInt(s, 10); return Number.isNaN(n) ? d : n; }, gwintManager: m,
        GwaiDiagnosticsArchive() {},
        OnW3GwentPlayTraceEnd(reason) { assert.equal(reason, 'interrupted'); },
        NameToString: String, FactsQueryLatestValue: key => { assert.equal(key,'gwent_difficulty');return env.nativeDifficulty; },
        isMainMenu: false, GwintFaction_Neutral: 0,
        wrappedMethod() { events.push('wrapped'); return false; },
        GetRootMenu() { return { CloseMenu() { events.push('close'); } }; },
        thePlayer: { state: 7, IsInCombat() { return false; },
            GetGwintMinigameState() { return this.state; },
            SetGwintMinigameState(v) { this.state = v; } },
        theGame: { GetInGameConfigWrapper() { return cfg; },
            SaveUserSettings() {},
            GetGwintManager() { return m; }, GetGuiManager(){return {ShowUserDialogAdv(...args){dialogs.push(args)}}}, IsDialogOrCutscenePlaying() { return false; },
            RequestMenu(v) { events.push(v); } }, ...overrides };
    const managerEnv = new Proxy(env, {
        has(t, k) { return k in m || k in t; },
        get(t, k) { const v = k in m ? m[k] : t[k]; return typeof v === 'function' && k in m ? v.bind(m) : v; },
        set(t, k, v) { if (k in m || k.startsWith('gwaiDev')) m[k] = v; else t[k] = v; return true; }
    });
    m.GwaiDevBegin = opponent => { managerEnv.opponent = opponent; run('GwaiDevBegin', managerEnv); };
    m.GwaiDevRestore = () => run('GwaiDevRestore', managerEnv);
    env.GwaiPracticeAI = () => run('GwaiPracticeAI', env);
    env.GwaiSettingsEnabled = () => run('GwaiSettingsEnabled', env);
    env.GwaiGwentDifficulty = () => run('GwaiGwentDifficulty', env);
    env.GwaiPracticeOpponent = id => run('GwaiPracticeOpponent', { ...env, id });
    env.GwaiPracticeOpponentLabel = id => run('GwaiPracticeOpponentLabel', { ...env, id });
    return { env, m, cfg, values, events, dialogs };
}
for (const [selection, deck] of [['0','NKHard'],['1','NilfHard'],['2','ScoiaHard'],['3','NMLHard'],['4','SkelHard'],['32','ScoiaTournament']]) {
    const f = fixture(); f.values.GwaiDevOpponent = selection;
    assert.equal(run('OnButtonClicked', f.env), true);
    assert.equal(f.m.selectedEnemyDeck, deck);
    assert.equal(Object.hasOwn(f.values,'GwaiDevStart'),false);
    assert.equal(f.m.gameRequested, true);
    assert.equal(f.m.testMatch, true);
    assert.deepEqual(f.events, ['close', 'DeckBuilder']);
    f.env.wrappedMethod = () => { f.env.thePlayer.state = 99; f.m.tutorial = true; f.m.deckTutorial = true; };
    run('matchClose', f.env);
    assert.equal(f.env.thePlayer.state, 7);
    assert.equal(f.m.tutorial, false); assert.equal(f.m.deckTutorial, false);
    assert.equal(f.m.selectedEnemyDeck, 'Thaler');
    assert.deepEqual(f.m.extra, ['bonus']); assert.equal(f.m.faction, 3);
    assert.equal(f.m.doubleAI, true); assert.equal(f.m.gwaiDevActive, false);
}
for (const block of ['main','player','root','combat','dialog','pending','testMatch','active','other','otherGroup']) {
    const f = fixture();
    if (block === 'main') f.env.isMainMenu = true;
    if (block === 'player') f.env.thePlayer = null;
    if (block === 'root') f.env.GetRootMenu = () => null;
    if (block === 'combat') f.env.thePlayer.IsInCombat = () => true;
    if (block === 'dialog') f.env.theGame.IsDialogOrCutscenePlaying = () => true;
    if (block === 'pending') f.m.gameRequested = true;
    if (block === 'testMatch') f.m.testMatch = true;
    if (block === 'active') f.m.gwaiDevActive = true;
    if (block === 'otherGroup') f.env.groupId = 2;
    if (block === 'other') f.env.optionName = 'GwaiEnabled';
    run('OnButtonClicked', f.env);
    assert.deepEqual(f.events, block === 'otherGroup' ? ['wrapped'] : [], block);
    assert.equal(f.m.selectedEnemyDeck, 'Thaler', block);
    assert.equal(f.dialogs.length,['other','otherGroup'].includes(block)?0:1,block);
    if(['main','player','root'].includes(block)) assert.ok(f.dialogs[0][2].includes('Load a saved game'),block);
    assert.equal(Object.hasOwn(f.values,'GwaiDevStart'),false);
}
for (const starting of [false, true]) {
    const f = fixture(); run('OnButtonClicked', f.env);
    f.m.gameRequested = starting;
    f.env.wrappedMethod = () => { f.env.thePlayer.state = 99; f.m.gameRequested = false; };
    run('deckClose', f.env);
    assert.equal(f.m.gwaiDevActive, starting);
    assert.equal(f.env.thePlayer.state, starting ? 99 : 7);
}
const f = fixture(); run('matchClose', f.env);
assert.equal(f.env.thePlayer.state, 7); // Closing a normal match does no restoration.
for (const handled of [false, true]) {
    const f = fixture(); f.env.groupId=2; f.env.optionName = 'GwaiEnabled';
    f.env.wrappedMethod = () => handled;
    assert.equal(run('OnButtonClicked', f.env), handled);
}
for(const enabled of [false,true]) for(const tier of [1,2,3]) {
    const f = fixture();f.values.GwaiEnabled=String(enabled);f.env.nativeDifficulty=tier;
    run('OnButtonClicked', f.env);
    assert.equal(f.m.gwaiDevAI,enabled?2:1);
    const packet = {}, storage = {
        CreateTempFlashObject() {
            return Object.fromEntries(['Bool','Int','String'].map(type =>
                ['SetMemberFlash' + type, (k, v) => { packet[k] = v; }]));
        }, SetFlashObject() {}
    };
    Object.assign(f.env, {
        GwaiSettingsInitialize() {}, GwaiDiagnosticsInitialize() {},
        GwaiSettingsSignal: () => true,GwaiSettingsToggleOn: k => k === 'GwaiStory',
        GwaiLabToggleOn: () => false, m_flashValueStorage: storage
    });
    run('configure', f.env);
    assert.equal(packet.enabled, enabled);
    assert.equal(packet.practice, true);
    assert.equal(packet.vanillaDifficulty,tier); // Chosen policy tier.
    assert.equal(Object.hasOwn(packet,'difficulty'),false);
    assert.equal(Object.hasOwn(packet,'clockEnabled'),false);
    assert.equal(Object.hasOwn(packet,'clockScale'),false);
    assert.equal(packet.practiceDeckDifficulty, tier); // Actual NPC deck still follows the unchanged game setting.
    assert.equal(packet.story, false);
    assert.equal(f.values.GwaiEnabled,String(enabled)); // Practice does not overwrite the global preference.
    f.m.GwaiDevRestore();
    run('configure', f.env);
    assert.equal(packet.enabled,enabled);
    assert.equal(packet.practice, false);
    assert.equal(packet.vanillaDifficulty, tier);
    assert.equal(packet.story, true);
}
{
    const f = fixture();
    for (const [enabled,opponent,mode,tier,deck] of [
        ['true','0',2,3,'NKHard'],
        ['false','16',1,1,'Lambert'],
        ['true','1',2,2,'NilfHard']
    ]) {
        Object.assign(f.values,{GwaiEnabled:enabled,GwaiDevOpponent:opponent});f.env.nativeDifficulty=tier;
        assert.equal(run('OnButtonClicked',f.env),true);
        assert.equal(f.m.gwaiDevAI,mode);
        assert.equal(f.m.selectedEnemyDeck,deck);
        f.m.GwaiDevRestore();
        assert.equal(f.m.gwaiDevAI,0);
        assert.equal(f.m.selectedEnemyDeck,'Thaler');assert.equal(f.values.GwaiEnabled,enabled);
    }
}
{
    const f = fixture(), dialogs = [];
    f.env.optionName = 'GwaiDevReport';
    f.env.UDB_Ok = 'OK';
    f.env.GwaiVerificationText = () => 'Current configuration<br>Latest recorded match';
    f.env.theGame.GetGuiManager = () => ({ ShowUserDialogAdv(...args) { dialogs.push(args); } });
    assert.equal(run('OnButtonClicked', f.env), true);
    assert.deepEqual(dialogs[0], [0, 'Gwent: Trained Opponents', 'Current configuration<br>Latest recorded match', false, 'OK']);
    assert.equal(Object.hasOwn(f.values,'GwaiDevReport'),false);
    assert.equal(f.m.gameRequested, false);
    assert.deepEqual(f.events, []);
}
""".replace("BODIES", json.dumps(bodies))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
