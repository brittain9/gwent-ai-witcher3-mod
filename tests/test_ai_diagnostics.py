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
        r"\b(\w+)\s*:\s*(String|Boolean|Error|int|string|bool|CInGameConfigWrapper)\b",
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
for(const tier of [-1,0,1,2,3,4,99]) {
 const saved={};const manager={gwaiDevTier:tier,gwaiDevAI:2,gwaiDevLabel:'test opponent'};
 const env={theGame:{GetInGameConfigWrapper(){return {SetRawConfigValueByStr(g,k,v){saved[k]=v}}},
 GetGwintManager(){return manager},SaveUserSettings(){}},IntToString:String,FactsQueryLatestValue(){return 2}};
 new Function('env','with(env){'+body+'}')(env);
 assert.equal(saved.policy_tier,String(tier>=1&&tier<=3?tier:3));
 assert.equal(saved.build,'0.1.0-preview');assert.equal(saved.vanilla_difficulty,'2');
}
""".replace("BODY", json.dumps(body))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_statistics_packet_contains_stock_and_exactly_three_trained_buckets(
        self,
    ) -> None:
        source = (ROOT / "mod/ai/settings/scripts/local/gwai_settings.ws").read_text()
        body = _method_body(source, "GwaiStatsSend")
        body = re.sub(r"var (\w+)\s*:\s*array<\s*string\s*>;", r"var \1=[];", body)
        body = (
            re.sub(r"\b(\w+)\s*:\s*(?:int|string)\b", r"\1", body)
            .replace(".PushBack(", ".push(")
            .replace(".Size()", ".length")
        )
        harness = r"""
const assert=require('node:assert/strict');const packet={},reads=[];
const env={packet:{SetMemberFlashInt(k,v){packet[k]=v}},deckName:'test',GwaiStatsCount(k){reads.push(k);return 1}};
new Function('env','with(env){'+BODY+'}')(env);
const expected=['vanilla','easy','normal','hard'].flatMap(tier=>['won','lost','drawn'].map(outcome=>'stats_'+tier+'_'+outcome));
expected.push(...['won','lost','drawn'].map(outcome=>'stats_deck_'+outcome));
assert.deepEqual(Object.keys(packet).sort(),expected.sort());
assert.ok(reads.every(key=>!key.startsWith('impossible')));
""".replace("BODY", json.dumps(body))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
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
assert.equal(saved.stock, '1'); // Ordinary stock games are untouched by practice diagnostics.
flow._gwaiSettings.practice = true;
flow.mcMessageQueue.PushMessage = () => { throw new Error('banner failed'); };
new Function('env', 'with(env){' + bodies.stock + '}').call(flow, env);
assert.equal(saved.stock, '2'); // Optional UI cannot interrupt the stock decision path.
Object.assign(saved, { practice: '2', policy_tier: '3', opponent: 'Scoia tournament' });
const reportEnv = { ...sinkEnv };
function verification() { return new Function('env', 'with(env){' + bodies.verification + '}')(reportEnv); }
reportEnv.GwaiVerificationText = verification;
const report = verification();
assert.ok(report.startsWith('Network used, with fallbacks'));
assert.ok(report.includes('Selected: Trained AI, tier 3'));
assert.ok(report.includes('Network choices: 1; selected stock: 2'));
assert.ok(report.includes('First error: capture failed: TypeError bad  value'));
for (let i = 0; i < 10; i++) {
    new Function('env', 'with(env){' + bodies.archive + '}')(reportEnv);
}
assert.equal(saved.history_next, '2');
assert.equal(Object.keys(saved).filter(k => /^history_\d+$/.test(k)).length, 8);
assert.ok(!saved.history_0.includes('\n') && !saved.history_0.includes('\r'));
Object.assign(saved, { network: '0', stock: '22', vanilla: '0', greedy: '0', practice: '1', first_error: '' });
assert.ok(verification().startsWith('Stock AI only'));
assert.ok(verification().includes('Selected: Stock game AI'));
Object.assign(saved, { stock: '0', vanilla: '3', practice: '2' });
assert.ok(verification().startsWith('No network move recorded'));
""".replace("BODIES", json.dumps(bodies))
        result = subprocess.run(
            ["node", "-e", harness], capture_output=True, text=True, check=False
        )
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
