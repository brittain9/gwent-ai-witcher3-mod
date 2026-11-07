from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.build_mod import FLOW_METHODS, _method_body

ROOT = Path(__file__).resolve().parents[1]


def js_body(source: str, name: str) -> str:
    body = _method_body(
        source.replace("event OnW3GwentAIDiagnostic", "function OnW3GwentAIDiagnostic"),
        name,
    )
    return re.sub(
        r"\b(\w+)\s*:\s*(String|Boolean|Error|int|string|bool|CInGameConfigWrapper|SDeckDefinition)\b",
        r"\1",
        body,
    )


@unittest.skipUnless(
    shutil.which("node"), "Node is required for diagnostic boundary checks"
)
class AIDiagnosticTests(unittest.TestCase):
    def test_new_diagnostics_keep_current_tier_in_the_three_tier_range(self) -> None:
        source = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text()
        body = js_body(source, "GwaiDiagnosticsInitialize")
        harness = r"""
const assert=require('node:assert/strict');const body=BODY;
for(const enabled of [false,true]) for(const mode of [1,2]) for(const tier of [-1,0,1,2,3,4,99]) {
 const saved={};const manager={gwaiDevActive:true,gwaiDevAI:mode,gwaiDevLabel:'test opponent',
 GetCurrentAIDeck(){throw new Error('practice diagnostics already have their requested opponent')}};
 Object.defineProperty(manager,'selectedEnemyDeck',{get(){throw new Error('native field is private')}});
 const env={theGame:{GetInGameConfigWrapper(){return {SetRawConfigValueByStr(g,k,v){saved[k]=v}}},
 GetGwintManager(){return manager},SaveUserSettings(){}},IntToString:String,FactsQueryLatestValue(){return tier},GwaiGwentDifficulty(){return Math.max(1,Math.min(3,tier))},GwaiSettingsEnabled(){return enabled},NameToString:String};
 new Function('env','with(env){'+body+'}')(env);
 assert.equal(saved.policy_tier,String(Math.max(1,Math.min(3,tier))));
 assert.equal(saved.build,'0.1.1');assert.equal(saved.vanilla_difficulty,String(tier));
 assert.equal(saved.enabled,String(enabled&&mode===2));assert.equal(saved.match_mode,'practice');
}
""".replace("BODY", json.dumps(body))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_story_diagnostics_record_requested_ai_and_native_tier(self) -> None:
        source = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text()
        body = js_body(source, "GwaiDiagnosticsInitialize")
        harness = r"""
const assert=require('node:assert/strict');const body=BODY;
for(const enabled of [false,true]) for(const [native,expected] of [[0,1],[1,1],[2,2],[3,3],[4,3]]) {
 let deckReads=0;const saved={};const manager={gwaiDevActive:false,gwaiDevAI:0,gwaiDevLabel:'',
 GetCurrentAIDeck(){deckReads++;return {deckName:'Lambert'}}};
 Object.defineProperty(manager,'selectedEnemyDeck',{get(){throw new Error('native field is private')}});
 const env={theGame:{GetInGameConfigWrapper(){return {SetRawConfigValueByStr(g,k,v){saved[k]=v}}},
 GetGwintManager(){return manager},SaveUserSettings(){}},IntToString:String,FactsQueryLatestValue(){return native},GwaiGwentDifficulty(){return Math.max(1,Math.min(3,native))},GwaiSettingsEnabled(){return enabled},NameToString:String};
 new Function('env','with(env){'+body+'}')(env);
 assert.equal(saved.policy_tier,String(expected));assert.equal(saved.enabled,String(enabled));
 assert.equal(saved.match_mode,'story');assert.equal(saved.opponent,'Lambert');
 assert.equal(deckReads,1);
 assert.equal(saved.vanilla_difficulty,String(native));
}
""".replace("BODY", json.dumps(body))
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reports_actual_decision_source_and_keeps_failures_with_banners_off(
        self,
    ) -> None:
        settings = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text()
        bodies = {
            "notify": js_body(FLOW_METHODS, "gwaiNotifyDecision"),
            "record": js_body(FLOW_METHODS, "gwaiRecordDecision"),
            "stock": js_body(FLOW_METHODS, "gwaiNotifyStockTurn"),
            "sink": js_body(settings, "OnW3GwentAIDiagnostic"),
            "verification": js_body(settings, "GwaiVerificationText"),
            "difficulty": js_body(settings, "GwaiDifficultyLabel"),
            "configuration": js_body(settings, "GwaiCurrentConfigurationText"),
            "latest": js_body(settings, "GwaiLatestMatchText"),
            "value": js_body(settings, "GwaiReportValue"),
            "archive": js_body(settings, "GwaiDiagnosticsArchive"),
        }
        harness = r"""
const assert = require('node:assert/strict');
const bodies = BODIES;
const saved = {}, messages = []; let flushes = 0;
const cfg = {
    GetRawConfigValueByStr(group, key) { return saved[key] || ''; },
    SetRawConfigValueByStr(group, key, value) { saved[key] = value; }
};
const sinkEnv = {
    theGame: { GetInGameConfigWrapper() { return cfg; }, SaveUserSettings() { flushes++; } },
    StringToInt: (s, d) => Number.parseInt(s, 10) || d,
    IntToString: String, StrLeft: (s, n) => s.slice(0, n),
    StrReplaceAll: (s, a, b) => s.split(a).join(b)
};
function sink(kind, detail) {
    return new Function('env', 'with(env){' + bodies.sink + '}')({ ...sinkEnv, kind, detail });
}
function GameEvent(type, name, args) { this.name = name; this.args = args; }
GameEvent.CALL = 'call';
const env = {
    red: { core: { events: { GameEvent } } },
    GwintGameMenu: { mSingleton: {
        dispatchEvent(event) {
            assert.equal(event.name, 'OnW3GwentAIDiagnostic');
            sink(...event.args);
        }
    } }
};
const flow = {
    _gwaiSettings: { signal: false }, _gwaiSignalShown: false,
    mcMessageQueue: { PushMessage(message) { messages.push(message); } }
};
flow.gwaiRecordDecision = (kind, detail) => new Function('env', 'kind', 'detail',
    'with(env){' + bodies.record + '}').call(flow, env, kind, detail);
function notify(fallback, detail) {
    return new Function('env', 'profile', 'fallback', 'detail',
        'with(env){' + bodies.notify + '}').call(flow, env, 3, fallback, detail);
}
notify(true, 'capture failed: TypeError=bad\r\nvalue');
assert.equal(saved.vanilla, '1');
assert.equal(saved.first_error, 'capture failed: TypeError bad  value');
assert.equal(messages.length, 0);
notify(true, 'transaction failed: second error');
assert.equal(saved.vanilla, '2');
assert.equal(saved.first_error, 'capture failed: TypeError bad  value');
assert.equal(saved.last_error, 'transaction failed: second error');
notify(false, 'play 1 network 20ms');
notify(false, 'pass greedy-fallback network 21ms');
assert.equal(saved.network, '1'); assert.equal(saved.greedy, '1');
assert.equal(messages.length, 0); // Signal-off suppresses successful and fallback banners alike.
sink('unknown', 'ignored'); assert.equal(saved.unknown, undefined);
flow._gwaiSettings.signal = true;
env.GwintGameMenu.mSingleton.dispatchEvent = () => { throw new Error('bridge failed'); };
notify(true, 'capture failed: third error');
assert.equal(messages.at(-1), 'W3 Gwent AI: fallback (vanilla AI plays this turn)');
assert.equal(saved.vanilla, '2'); // Failed diagnostics cannot interrupt the vanilla hand-back.
assert.equal(flushes, 3); // First fallback, first network, first greedy; repeated errors wait for match close.
notify(false, 'play 2 network 22ms');
assert.equal(messages.at(-1), 'W3 Gwent AI: Hard play 2 network 22ms');
notify(false, 'pass greedy-fallback network 23ms');
assert.equal(messages.at(-1), 'W3 Gwent AI: Hard pass greedy-fallback network 23ms');
env.GwintGameMenu.mSingleton.dispatchEvent = event => sink(...event.args);
flow._gwaiSettings = { signal: true, practice: true, enabled: false };
new Function('env', 'with(env){' + bodies.stock + '}').call(flow, env);
assert.equal(saved.stock, '1');
assert.equal(saved.vanilla, '2');
assert.equal(messages.at(-1), 'W3 Gwent AI: stock AI (selected for practice)');
flow._gwaiSettings.practice = false;
new Function('env', 'with(env){' + bodies.stock + '}').call(flow, env);
assert.equal(saved.stock, '2'); // Story selection is observable as well as practice selection.
assert.equal(messages.at(-1), 'W3 Gwent AI: stock AI (selected for story)');
flow._gwaiSettings.practice = true;
flow.mcMessageQueue.PushMessage = () => { throw new Error('banner failed'); };
new Function('env', 'with(env){' + bodies.stock + '}').call(flow, env);
assert.equal(saved.stock, '3'); // Optional UI cannot interrupt the stock decision path.
Object.assign(saved, { build:'0.1.1',practice: '2', enabled:'true', policy_tier: '3', opponent: 'Scoia tournament' });
const reportEnv = { ...sinkEnv };
reportEnv.GwaiDifficultyLabel = tier=>new Function('tier',bodies.difficulty)(tier);
reportEnv.GwaiSettingsEnabled=()=>true;reportEnv.GwaiGwentDifficulty=()=>3;reportEnv.GwaiSettingsToggleOn=()=>false;
reportEnv.GwaiReportValue=(value,richText)=>new Function('env','value','richText','with(env){'+bodies.value+'}')(reportEnv,value,richText);
reportEnv.GwaiCurrentConfigurationText=separator=>new Function('env','separator','with(env){'+bodies.configuration+'}')(reportEnv,separator);
reportEnv.GwaiLatestMatchText=(separator,richText)=>new Function('env','separator','richText','with(env){'+bodies.latest+'}')(reportEnv,separator,richText);
function verification() { return new Function('env', 'with(env){' + bodies.verification + '}')(reportEnv); }
reportEnv.GwaiVerificationText = verification;
const report = verification();
assert.ok(report.startsWith('Current configuration'));
assert.ok(report.includes('Network used, with fallbacks'));
assert.ok(report.includes('Requested AI: Trained AI (Hard)'));
assert.ok(report.includes('Network choices: 1; selected stock: 3'));
assert.ok(report.includes('First error: capture failed: TypeError bad  value'));
for(const [tier,label] of [['1','Easy'],['2','Medium'],['3','Hard']]) {
    saved.policy_tier=tier;
    assert.ok(verification().includes('Requested AI: Trained AI ('+label+')'));
}
for (let i = 0; i < 10; i++) {
    new Function('env', 'with(env){' + bodies.archive + '}')(reportEnv);
}
assert.equal(saved.history_next, '2');
assert.equal(Object.keys(saved).filter(k => /^history_\d+$/.test(k)).length, 8);
assert.ok(!saved.history_0.includes('\n') && !saved.history_0.includes('\r'));
assert.ok(saved.history_0.startsWith('Latest recorded match | '));
assert.ok(!saved.history_0.includes('<br>'));assert.ok(!saved.history_0.includes('Current configuration'));
Object.assign(saved, { network: '0', stock: '22', vanilla: '0', greedy: '0', practice: '1', enabled:'false', first_error: '' });
assert.ok(verification().includes('Stock AI only'));
assert.ok(verification().includes('Requested AI: Stock game AI'));
Object.assign(saved, { stock: '0', vanilla: '3', practice: '2' });
assert.ok(verification().includes('Fallback AI used; no network choice recorded'));
""".replace("BODIES", json.dumps(bodies))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_status_separates_current_settings_from_recorded_match_and_escapes_markup(
        self,
    ) -> None:
        source = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text()
        bodies = {
            name: js_body(source, name)
            for name in (
                "GwaiVerificationText",
                "GwaiCurrentConfigurationText",
                "GwaiLatestMatchText",
                "GwaiReportValue",
                "GwaiDifficultyLabel",
                "GwaiSettingsEnabled",
                "GwaiGwentDifficulty",
                "GwaiSettingsToggleOn",
                "GwaiDiagnosticsArchive",
            )
        }
        harness = r"""
const assert=require('node:assert/strict');const bodies=BODIES;
const sections={W3GwentAI:{GwaiEnabled:'true',GwaiDevTier:'1',GwaiStory:'false'},W3GwentAIDiagnostics:{}};
const cfg={GetRawConfigValueByStr(group,key){return sections[group]?.[key]??''},
SetRawConfigValueByStr(group,key,value){sections[group][key]=value}};
const env={FactsQueryLatestValue:()=>2,theGame:{GetInGameConfigWrapper(){return cfg}},IntToString:String,
StringToInt(s,fallback){const n=Number.parseInt(s,10);return Number.isNaN(n)?fallback:n},
StrLeft:(s,n)=>s.slice(0,n),StrReplaceAll:(s,a,b)=>s.split(a).join(b)};
function run(name,args={}){return new Function('env','with(env){'+bodies[name]+'}')({...env,...args})}
env.GwaiDifficultyLabel=tier=>run('GwaiDifficultyLabel',{tier});
env.GwaiSettingsEnabled=()=>run('GwaiSettingsEnabled');env.GwaiGwentDifficulty=()=>run('GwaiGwentDifficulty');
env.GwaiSettingsToggleOn=id=>run('GwaiSettingsToggleOn',{id});
env.GwaiReportValue=(value,richText)=>run('GwaiReportValue',{value,richText});
env.GwaiCurrentConfigurationText=separator=>run('GwaiCurrentConfigurationText',{separator});
env.GwaiLatestMatchText=(separator,richText)=>run('GwaiLatestMatchText',{separator,richText});
let report=run('GwaiVerificationText');
assert.ok(report.includes('Current configuration<br>AI: Trained network'));
assert.ok(report.includes('Gwent difficulty: Medium (game setting)'));
assert.ok(report.includes('Latest recorded match<br>No match recorded yet.'));
assert.ok(!report.includes('Stock game AI'));assert.ok(!report.includes('Opponent:'));
assert.ok(!report.includes('\\n'));
Object.assign(sections.W3GwentAIDiagnostics,{build:'0.1.1',enabled:'false',policy_tier:'1',match_mode:'story',
opponent:'<font color="red">Lambert & friends</font>',network:'0',stock:'3',vanilla:'1',greedy:'0',first_error:'bad <tag> & input'});
report=run('GwaiVerificationText');
assert.ok(report.includes('AI: Trained network'));assert.ok(report.includes('Requested AI: Stock game AI'));
assert.ok(report.includes('&lt;font color="red"&gt;Lambert &amp; friends&lt;/font&gt;'));
assert.ok(report.includes('First error: bad &lt;tag&gt; &amp; input'));assert.ok(!report.includes('<font'));
run('GwaiDiagnosticsArchive');
const archived=sections.W3GwentAIDiagnostics.history_0;
assert.ok(archived.includes('Lambert & friends'));assert.ok(archived.includes('bad <tag> & input'));
assert.ok(!archived.includes('<br>'));assert.ok(!archived.includes('&lt;'));assert.ok(!archived.includes('Current configuration'));
sections.W3GwentAI.GwaiEnabled='false';
Object.assign(sections.W3GwentAIDiagnostics,{enabled:'true',policy_tier:'3',match_mode:'practice',network:'4',stock:'0',vanilla:'0',opponent:''});
report=run('GwaiVerificationText');
assert.ok(report.includes('Current configuration<br>AI: Stock game AI'));
assert.ok(report.includes('Requested AI: Trained AI (Hard)'));assert.ok(report.includes('Opponent: Not recorded'));
assert.ok(report.includes('Network used'));
""".replace("BODIES", json.dumps(bodies))
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class SignalDefaultTests(unittest.TestCase):
    def test_only_an_explicit_opt_in_enables_debug_messages(self):
        import json
        import re

        from scripts.build_mod import _method_body

        source = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text()
        body = _method_body(source, "GwaiSettingsSignal")
        body = re.sub(r"var raw\s*:\s*string", "var raw", body)
        script = "const f = new Function('theGame', " + json.dumps(body) + ");"
        script += "process.stdout.write(JSON.stringify(['','false','0','invalid','true','1'].map(raw => f({GetInGameConfigWrapper(){return {GetRawConfigValueByStr(){return raw}}}}))));"
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(
            json.loads(result.stdout), [False, False, False, False, True, True]
        )
