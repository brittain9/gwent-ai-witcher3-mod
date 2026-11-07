from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import mod_trace
from scripts.build_mod import _method_body, patch_sources
from scripts.harvest_playtrace import (
    PlayTraceError,
    harvest,
    read_traces,
    timeline,
    validate_events,
)
from tests.test_mod_package import MINI_HELPERS

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / ".local/as3/scripts/red/game/witcher3/menus/gwint"


def run_node(source: str) -> str:
    result = subprocess.run(
        [shutil.which("node"), "-e", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


def js_body(source: str, method: str) -> str:
    source = re.sub(r"\bevent\s+", "function ", source)
    body = _method_body(source, method)
    # Native preserves the two separator bytes before StrMid chunking.
    body = body.replace('"\\n"', '"\\\\n"')
    return re.sub(
        r"\b(\w+)\s*:\s*(?:CInGameConfigWrapper|string|int|bool)\b", r"\1", body
    )


def helper_javascript() -> str:
    source = mod_trace.helper_source("test-build")
    body = source[source.index("{") + 1 : source.rfind("}")]
    body = re.sub(r"private static var", "var", body)
    body = re.sub(r"(?:public|private) static function", "function", body)
    body = re.sub(r"\)\s*:\s*(?:void|Object|Array|Boolean|String)\s*\{", ") {", body)
    body = re.sub(r"\b(\w+)\s*:\s*\*", r"\1", body)
    body = body.replace(":Vector.<CardInstance>", "")
    body = re.sub(
        r"\b(\w+)\s*:\s*(?:Vector\.<CardInstance>|CardTransaction|CardInstance|CardManager|GwintGameFlowController|GwintRoundResult|Boolean|String|Object|Number|Array|int|Error)\b(?!\s*\()",
        r"\1",
        body,
    )
    body = re.sub(r"catch\((\w+):Error\)", r"catch(\1)", body)
    body = body.replace("value is Array", "Array.isArray(value)")
    return body


@unittest.skipUnless(
    shutil.which("node"), "Node is required for the real AS3/sink boundary checks"
)
class TraceRuntimeTests(unittest.TestCase):
    def test_actual_helper_flushes_public_events_when_runtime_has_no_json(self) -> None:
        source = mod_trace.SINK_PATH.read_text()
        bodies = {
            name: js_body(source, name)
            for name in (
                "GwaiPlayTracePad",
                "OnW3GwentPlayTraceStart",
                "OnW3GwentPlayTraceRecord",
                "OnW3GwentPlayTraceEnd",
                "OnW3GwentPlayTraceError",
            )
        }
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');const bodies=BODIES;
const saved={},snapshots=[],records=[];
const cfg={GetRawConfigValueByStr(g,k){return saved[g+'/'+k]||''},
 SetRawConfigValueByStr(g,k,v){saved[g+'/'+k]=String(v)}};
saved['W3GwentAI/GwaiTrace']='true';
const env={gwaiTraceActive:false,gwaiTraceMatchId:0,gwaiTraceChunkIndex:0,
 theGame:{GetInGameConfigWrapper(){return cfg},SaveUserSettings(){snapshots.push({...saved})}},
 IntToString:String,StringToInt:(s,d)=>Number.isNaN(parseInt(s,10))?d:parseInt(s,10),
 StrLen:s=>s.length,StrMid:(s,i,n)=>s.substr(i,n),StrLeft:(s,n)=>s.slice(0,n)};
function run(n,args={}){Object.assign(env,args);return new Function('env','with(env){'+bodies[n]+'}')(env)}
env.GwaiPlayTracePad=value=>run('GwaiPlayTracePad',{value});
function GameEvent(type,name,args){this.name=name;this.args=args}GameEvent.CALL='call';
const CardManager={PLAYER_1:0,PLAYER_2:1,PLAYER_INVALID:-1,CARD_LIST_LOC_HAND:1,CARD_LIST_LOC_MELEE:2};
const label=('Quote " backslash \\ tab\t zero\0 CR\r LF\n BS\b FF\f '+String.fromCharCode(0x1f,0x7f,0x85,0x2028,0x2029)+' é Œ 🐺 ').repeat(40);
const publicCard={instanceId:20,templateId:100,templateRef:{title:label},owningPlayer:0,listsPlayer:0,getTotalPower(){return 4}};
const manager={roundResults:[],currentPlayerScores:[0,0],
 playerDeckDefinitions:[{cardIndicesInDeck:{length:12}},{cardIndicesInDeck:{length:12}}],
 getCardInstanceList(list,seat){return list===1?{length:10}:list===2&&seat===0?[publicCard]:[]}};
const flow={currentPlayer:0,playerControllers:[{currentRoundStatus:1},{currentRoundStatus:1}]};
const sandbox={JSON:undefined,CardManager,BasePlayerController:{ROUND_PLAYER_STATUS_DONE:2},
 red:{core:{events:{GameEvent}}},GwintGameMenu:{mSingleton:{dispatchEvent(e){
 if(e.name==='OnW3GwentPlayTraceStart')run(e.name,{build:e.args[0]});
 else if(e.name==='OnW3GwentPlayTraceRecord'){records.push(JSON.parse(e.args[0]));run(e.name,{record:e.args[0]});}
 else if(e.name==='OnW3GwentPlayTraceEnd')run(e.name,{reason:e.args[0]});
 else if(e.name==='OnW3GwentPlayTraceError')run(e.name,{message:e.args[0]});
 }}}};
const api=vm.runInNewContext(HELPER+'\n({begin,decision,failed,emitInvalid(){emit({event:"invalid",bad:NaN})}})',sandbox);
try{api.begin(true,manager,flow,1);api.decision('network','private chosen card omitted')}
catch(e){try{api.failed()}catch(ignored){}}
const prefix=saved['W3GwentPlayTrace/active_match'];
assert.ok(Number(saved[`W3GwentPlayTrace/${prefix}_count`])>0,'native runtime without JSON must flush real event chunks');
const count=Number(saved[`W3GwentPlayTrace/${prefix}_count`]);let body='';
for(let i=0;i<count;i++){
 const chunk=saved[`W3GwentPlayTrace/${prefix}_${String(i).padStart(9,'0')}`];
 assert.ok(chunk.length<=780);assert.ok(/^[\x00-\x7f]*$/.test(chunk));body+=chunk;
}
assert.ok(body.endsWith('\\n'));
assert.equal(records[0].event,'match_start');assert.equal(records[1].source,'network');
assert.equal(records[0].state.players[0].rows.melee[0].name,label);
assert.ok(snapshots.length>=3,'each source event must persist through the native sink');
// Invalid native values must stop tracing through a plain-string channel. The
// prefix remains harvestable; errors never try to encode another error event.
assert.doesNotThrow(()=>api.emitInvalid());
assert.equal(saved[`W3GwentPlayTrace/${prefix}_status`],'error');
assert.equal(saved[`W3GwentPlayTrace/${prefix}_error`],'encode: Error: public trace non-finite number');
assert.equal(Number(saved[`W3GwentPlayTrace/${prefix}_count`]),count);
const afterFailure=snapshots.length;api.decision('network','ignored after trace failure');
assert.equal(snapshots.length,afterFailure);
// An exception before emit must report its originating hook through the same
// JSON-independent channel, without copying arbitrary private error text.
manager.getCardInstanceList=()=>{throw new TypeError('PRIVATE-STATE')};
try{api.begin(true,manager,flow,1)}catch(error){api.failed(error,'GwaiPlayTrace.begin')}
assert.equal(saved['W3GwentPlayTrace/t000000002_count'],'0');
assert.equal(saved['W3GwentPlayTrace/t000000002_status'],'error');
assert.equal(saved['W3GwentPlayTrace/t000000002_error'],'GwaiPlayTrace.begin: TypeError');
assert.ok(!JSON.stringify(saved).includes('PRIVATE-STATE'));
console.log(JSON.stringify({records,saved}));
""".replace("BODIES", json.dumps(bodies)).replace(
            "HELPER", json.dumps(helper_javascript())
        )
        result = json.loads(run_node(harness))
        validate_events(result["records"], "test-build", "error")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings"
            values = {
                key.split("/", 1)[1]: value
                for key, value in result["saved"].items()
                if key.startswith("W3GwentPlayTrace/")
            }
            path.write_text(native_settings_text(values))
            traces = read_traces(path)
            self.assertEqual(traces["t000000001"]["events"], result["records"])
            self.assertIn(
                "public trace non-finite number",
                timeline("t000000001", traces["t000000001"]),
            )
            self.assertEqual(traces["t000000002"]["events"], [])
            self.assertIn(
                "metadata alone does not prove",
                timeline("t000000002", traces["t000000002"]),
            )

    def test_constrained_encoder_rejects_invalid_values_and_escapes_unicode_without_json(
        self,
    ) -> None:
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const encoded=vm.runInNewContext(HELPER+`
 const text = String.fromCharCode(0,8,9,10,12,13,31,34,92,127,133,0x2028,0x2029,0xd83d,0xdc3a);
 const result = encodeValue({text:text,number:1e-8,negative:-2.5,array:[true,false,null,0]},0);
 const cyclic={};cyclic.self=cyclic;
 var rejected=0;
 for(const invalid of [undefined,NaN,Infinity,-Infinity,function(){},new Date(),/x/,Array(1),cyclic]) {
  try {encodeValue(invalid,0)}catch(error){rejected++}
 }
 ({result,text,rejected});`,{JSON:undefined});
assert.equal(encoded.rejected,9);assert.ok(/^[\x20-\x7e]+$/.test(encoded.result));
assert.deepEqual(JSON.parse(encoded.result),{text:encoded.text,number:1e-8,negative:-2.5,array:[true,false,null,0]});
console.log('ok');
""".replace("HELPER", json.dumps(helper_javascript()))
        self.assertEqual(run_node(harness).strip(), "ok")

    def test_actual_public_helper_never_serializes_hidden_state_and_confirms_native_commits(
        self,
    ) -> None:
        harness = r"""
const assert = require('node:assert/strict');
const events = [], calls = [];
const int = x => Number(x) | 0;
function GameEvent(type,name,args) { this.name=name; this.args=args; }
GameEvent.CALL='call';
const red={core:{events:{GameEvent}}};
const GwintGameMenu={mSingleton:{dispatchEvent(e) {
 calls.push(e.name); if(e.name==='OnW3GwentPlayTraceRecord') events.push(JSON.parse(e.args[0]));
}}};
const CardManager={PLAYER_1:0,PLAYER_2:1,PLAYER_INVALID:-1,getInstance(){return manager},
 CARD_LIST_LOC_HAND:1,CARD_LIST_LOC_MELEE:2,CARD_LIST_LOC_RANGED:3,CARD_LIST_LOC_SEIGE:4,
 CARD_LIST_LOC_GRAVEYARD:5,CARD_LIST_LOC_LEADER:6,CARD_LIST_LOC_MELEEMODIFIERS:7,
 CARD_LIST_LOC_RANGEDMODIFIERS:8,CARD_LIST_LOC_SEIGEMODIFIERS:9,CARD_LIST_LOC_WEATHERSLOT:10};
const BasePlayerController={ROUND_PLAYER_STATUS_DONE:2};
HELPER
const hidden = new Proxy({length:3},{get(t,k) {
 if(k==='length') return t.length; throw Error('hidden hand/deck contents read');
}});
const publicRows={};
let noCards=false,leaderUsable=false;
const manager={roundResults:[],currentPlayerScores:[0,0],getCardLeader(){return {canBeUsed:leaderUsable}},
 playerDeckDefinitions:[{cardIndicesInDeck:hidden},{cardIndicesInDeck:hidden}],
 getCardInstanceList(list,seat){ return list===1?(noCards?[]:hidden):(publicRows[`${list}:${seat}`]||[]); }};
const flow={currentPlayer:0,playerControllers:[{currentRoundStatus:1},{currentRoundStatus:1}]};
begin(false,null,null,1); actionStarted(1,null); actionCommitted(); stable(null,null,1,1);
decision('network','play PRIVATE-CHOICE network 1ms'); roundComplete(0);
assert.equal(events.length,0); assert.equal(calls.length,0);
begin(true,manager,flow,1);
const selection={enabled:true,requested_tier:2,effective_tier:3,mode:'story',opponent:'Lambert',
 npc_difficulty:2,checkpoint_sha256:'a'.repeat(64),weights_sha256:'b'.repeat(64),secret:'PRIVATE-HAND'};
decision('network','play PRIVATE-CHOICE network 1ms',selection);
assert.equal(events.at(-1).selection.effective_tier,3);
assert.equal(events.at(-1).selection.requested_tier,2);
assert.equal(events.at(-1).selection.checkpoint_sha256,'a'.repeat(64));
assert.equal(events.at(-1).selection.secret,undefined);
decision('vanilla','vocabulary failed: TypeError: Error #1009 PRIVATE-CHOICE');
const card={instanceId:20,templateId:100,templateRef:{title:'Scorch'},owningPlayer:1,listsPlayer:1,getTotalPower(){return 0}};
const transaction={sourceCardInstanceRef:card,targetCardInstanceRef:null,targetSlotID:-1,targetPlayerID:-1};
actionStarted(1,transaction);
assert.ok(!JSON.stringify(events).includes('Scorch'),'pending AI card must stay private');
// A committed Scorch may have been consumed/banished already. It still counts
// as played even though no current public zone contains its source instance.
actionCommitted(); stable(manager,flow,1,1);
assert.equal(events.at(-1).event,'action');
assert.equal(events.at(-1).transaction.source.name,'Scorch');
assert.equal(events.at(-1).state.players[1].hand_count,3);
card.templateRef.title='PRIVATE-CANCELLED'; actionStarted(1,transaction);
stable(manager,flow,1,1); assert.equal(events.at(-1).event,'action_cancelled');
choice(1,'handleBinChoice'); roundComplete(0);assert.equal(events.at(-1).event,'pass');
noCards=true;roundComplete(1);assert.equal(events.at(-1).event,'round_complete');
assert.equal(events.at(-1).reason,'no_cards');
leaderUsable=true;roundComplete(1);assert.equal(events.at(-1).event,'pass');
noCards=false;leaderUsable=false;
manager.roundResults.push({played:true,getRoundWinner(){return 1},getPlayerScore(s){return s?10:4}});
stateEvent('round_end',manager,flow,1);
finish('final',1,manager,flow,1);
assert.equal(events.at(-1).event,'match_end');
assert.ok(!JSON.stringify(events).includes('PRIVATE'),'private identities/details must not reach events');
assert.deepEqual(events.map(x=>x.seq),events.map((_,i)=>i));
assert.deepEqual(events.at(-1).state.round_results,[{winner:1,scores:[4,10]}]);
const completed=events.slice();
begin(true,manager,flow,1); const beforeFailure=events.length; failed();
assert.equal(events.length,beforeFailure);
assert.equal(calls.at(-1),'OnW3GwentPlayTraceError');
const count=events.length; roundComplete(1); assert.equal(events.length,count);
GwintGameMenu.mSingleton.dispatchEvent=()=>{throw Error('sink failed')};
assert.doesNotThrow(()=>begin(true,manager,flow,1));
assert.doesNotThrow(()=>roundComplete(0));assert.equal(events.length,count);
console.log(JSON.stringify(completed));
""".replace("HELPER", helper_javascript())
        events = json.loads(run_node(harness))
        validate_events(events, "test-build", "final")
        selected = next(event for event in events if "selection" in event)
        selected["selection"]["hand"] = ["hidden"]
        with self.assertRaisesRegex(PlayTraceError, "unexpected fields"):
            validate_events(events, "test-build", "final")

    def test_sink_flushes_every_event_retains_interrupted_matches_and_never_reuses_keys(
        self,
    ) -> None:
        source = mod_trace.SINK_PATH.read_text()
        bodies = {
            name: js_body(source, name)
            for name in (
                "GwaiPlayTracePad",
                "OnW3GwentPlayTraceStart",
                "OnW3GwentPlayTraceRecord",
                "OnW3GwentPlayTraceEnd",
            )
        }
        harness = r"""
const assert=require('node:assert/strict'); const bodies=BODIES;
const saved={}, writes=[], snapshots=[];
const cfg={GetRawConfigValueByStr(g,k){return saved[g+'/'+k]||''},
 SetRawConfigValueByStr(g,k,v){saved[g+'/'+k]=String(v);writes.push([g+'/'+k,String(v)])}};
const env={gwaiTraceActive:false,gwaiTraceMatchId:0,gwaiTraceChunkIndex:0,
 theGame:{GetInGameConfigWrapper(){return cfg},SaveUserSettings(){snapshots.push({...saved})}},
 IntToString:String, StringToInt:(x,d)=>Number.isNaN(parseInt(x,10))?d:parseInt(x,10),
 StrLen:s=>s.length, StrMid:(s,start,n)=>s.substr(start,n)};
function run(n,args={}) {Object.assign(env,args);return new Function('env','with(env){'+bodies[n]+'}')(env)}
env.GwaiPlayTracePad=value=>run('GwaiPlayTracePad',{value});
assert.equal(run('OnW3GwentPlayTraceStart',{build:'test-build'}),false);assert.equal(writes.length,0);
cfg.SetRawConfigValueByStr('W3GwentAI','GwaiTrace','true');
run('OnW3GwentPlayTraceStart',{build:'test-build'});
const first=saved['W3GwentPlayTrace/active_match'];
run('OnW3GwentPlayTraceRecord',{record:'x'.repeat(1900)});
assert.equal(saved[`W3GwentPlayTrace/${first}_count`],'3');
assert.equal(saved[`W3GwentPlayTrace/${first}_000000000`].length,780);
assert.equal(saved[`W3GwentPlayTrace/${first}_000000002`].length,342);
assert.ok(snapshots.at(-1)[`W3GwentPlayTrace/${first}_000000002`]);
// Simulate process exit: only saved settings survive; the next start must
// preserve and mark the prior active trace, even after its local fields vanish.
env.gwaiTraceActive=false;env.gwaiTraceMatchId=0;env.gwaiTraceChunkIndex=0;
cfg.SetRawConfigValueByStr('W3GwentPlayTrace','next_match','0');
run('OnW3GwentPlayTraceStart',{build:'test-build'});
const second=saved['W3GwentPlayTrace/active_match'];
assert.notEqual(first,second);assert.equal(saved[`W3GwentPlayTrace/${first}_status`],'interrupted');
assert.equal(saved[`W3GwentPlayTrace/${first}_000000000`],'x'.repeat(780));
run('OnW3GwentPlayTraceRecord',{record:'second'});run('OnW3GwentPlayTraceEnd',{reason:'final'});
assert.equal(saved[`W3GwentPlayTrace/${second}_status`],'final');
assert.equal(run('OnW3GwentPlayTraceEnd',{reason:'interrupted'}),false);
assert.equal(saved[`W3GwentPlayTrace/${second}_status`],'final');
run('OnW3GwentPlayTraceStart',{build:'test-build'});
env.gwaiTraceChunkIndex=20000;
assert.equal(run('OnW3GwentPlayTraceRecord',{record:'too much'}),false);
const third='t'+env.GwaiPlayTracePad(env.gwaiTraceMatchId);
assert.equal(saved[`W3GwentPlayTrace/${third}_status`],'truncated');
assert.equal(saved['W3GwentPlayTrace/active_match'],'');
console.log('ok');
""".replace("BODIES", json.dumps(bodies))
        self.assertEqual(run_node(harness).strip(), "ok")

    @unittest.skipUnless(
        (NATIVE / "BasePlayerController.as").is_file(),
        "native source extract unavailable",
    )
    def test_native_hooks_observe_both_controller_stable_paths_and_commits_after_mutation(
        self,
    ) -> None:
        names = {
            "AIPlayerController",
            "GwintGameFlowController",
            "GwintGameMenu",
            *mod_trace.EXTRA_CLASSES,
        }
        originals = {name: (NATIVE / f"{name}.as").read_text() for name in names}
        ai = patch_sources(
            *(
                originals[name]
                for name in (
                    "AIPlayerController",
                    "GwintGameFlowController",
                    "GwintGameMenu",
                )
            ),
            MINI_HELPERS,
        )
        patched = mod_trace.patch_sources({**originals, **ai})
        mod_trace.verify_compiled(patched, mod_trace.helper_source("test-build"))
        source = patched["BasePlayerController"]
        bodies = {
            name: _method_body(source, name)
            for name in (
                "transferTransactionCardToDestination",
                "applyTransactionCardToCardInstance",
                "applyGlobalEffectTransactionCard",
            )
        }
        human = _method_body(
            patched["HumanPlayerController"], "state_update_ApplyingCard"
        )
        stable = _method_body(
            patched["BasePlayerController"], "state_update_ApplyingCard"
        )
        ready = _method_body(
            patched["GwintGameFlowController"], "state_update_RoundStart"
        )
        nurse = _method_body(patched["CardInstance"], "handleNurseChoice")
        nurse = re.sub(r"\b(\w+)\s*:\s*\w+\b", r"\1", nurse)
        harness = (
            r"""
const assert=require('node:assert/strict');const events=[];const bodies=BODIES;
const trace={actionCommitted(){events.push('committed')},failed(){events.push('failed')}};
const manager={addCardInstanceIDToList(){events.push('move')},replaceCardInstanceIDs(){events.push('replace')},
 applyCardEffectsID(){events.push('effects')},sendToGraveyardID(){events.push('graveyard')}};
const CardManager={getInstance(){return manager}};
const ctx={_transactionCard:{instanceId:20},transactionCard:{},mcChoiceDialog:{visible:false},
 gameFlowControllerRef:{gwaiTraceStable(){events.push('stable')},playerControllers:[{currentRoundStatus:1}],mcMessageQueue:{ShowingMessage(){return false}}},
 _stateMachine:{ChangeState(){events.push('next')}},opponentID:0,cardEffectApplying(){return false}};
const env={CardManager,GwaiPlayTrace:trace,slotID:2,playerID:1,cardInstance:{instanceId:10},ROUND_PLAYER_STATUS_DONE:2,
 CardTweenManager:{getInstance(){return {isAnyCardMoving(){return false}}}},
 CardFXManager:{getInstance(){return {isPlayingAnyCardFX(){return false}}}},
 gameFlowControllerRef:ctx.gameFlowControllerRef,mcChoiceDialog:{visible:false},_stateMachine:ctx._stateMachine,_turnOver:false};
function exec(body){return new Function('env','with(env){'+body.replace(/catch\((\w+):Error\)/g,'catch($1)')+'}').call(ctx,env)}
exec(bodies.transferTransactionCardToDestination);assert.deepEqual(events,['move','committed']);events.length=0;
exec(bodies.applyTransactionCardToCardInstance);assert.deepEqual(events,['replace','committed']);events.length=0;
exec(bodies.applyGlobalEffectTransactionCard);assert.deepEqual(events,['effects','graveyard','committed']);events.length=0;
exec(STABLE);assert.deepEqual(events,['stable','next']);events.length=0;
ctx.on_state_about_to_update=()=>{};exec(HUMAN);assert.deepEqual(events,['stable','next']);events.length=0;
manager.addCardInstanceIDToList=()=>{throw Error('native failure')};
assert.throws(()=>exec(bodies.transferTransactionCardToDestination),/native failure/);assert.deepEqual(events,[]);
// Faction callbacks run while the message queue is showing. A round snapshot
// must wait for that queue, the transition guard and both native status resets.
let showing=true,blocked=false;
const roundFlow={currentRound:1,currentPlayer:0,cardManager:{},
 mcMessageQueue:{ShowingMessage(){return showing}},mcTutorials:{visible:false},
 shouldDisallowStateChangeFunc(){return blocked},
 playerControllers:[0,1].map(seat=>({currentRoundStatus:1,resetCurrentRoundStatus(){events.push('reset'+seat)}})),
 stateMachine:{ChangeState(){events.push('turn')}}};
CardManager.PLAYER_1=0;CardManager.PLAYER_2=1;const BasePlayerController={ROUND_PLAYER_STATUS_DONE:2};
env.BasePlayerController=BasePlayerController;
trace.stateEvent=(kind,manager,flow)=>{assert.deepEqual(events,['reset0','reset1']);events.push(kind)};
function runAt(body,context){return new Function('env','with(env){'+body.replace(/catch\((\w+):Error\)/g,'catch($1)')+'}').call(context,env)}
runAt(READY,roundFlow);assert.deepEqual(events,[]);
showing=false;blocked=true;runAt(READY,roundFlow);assert.deepEqual(events,[]);
blocked=false;runAt(READY,roundFlow);assert.deepEqual(events,['reset0','reset1','round_start','turn']);events.length=0;
// A stolen medic acts for its board seat, not its original owner. Stop at the
// first native boundary after observing the actual prepended trace call.
trace.choice=(seat,kind)=>events.push([seat,kind]);
manager.getCardInstance=()=>{throw Error('native boundary')};env.instanceId=10;
assert.throws(()=>runAt(NURSE,{listsPlayer:1,owningPlayer:0}),/native boundary/);
assert.deepEqual(events,[[1,'handleNurseChoice']]);
""".replace("BODIES", json.dumps(bodies))
            .replace("STABLE", json.dumps(stable))
            .replace("HUMAN", json.dumps(human))
            .replace("READY", json.dumps(ready))
            .replace("NURSE", json.dumps(nurse))
        )
        run_node(harness)


def public_state() -> dict:
    return {
        "round": 1,
        "actor": 0,
        "round_results": [],
        "weather": [],
        "players": [
            {
                "seat": seat,
                "score": 0,
                "hand_count": 10,
                "deck_count": 12,
                "passed": False,
                "rows": {row: [] for row in ("melee", "ranged", "siege")},
                "modifiers": {row: [] for row in ("melee", "ranged", "siege")},
                "leader": [],
                "graveyard": [],
            }
            for seat in (0, 1)
        ],
    }


def fixture(final: bool = True) -> list[dict]:
    records = [
        {
            "seq": 0,
            "event": "match_start",
            "schema": mod_trace.SCHEMA,
            "schema_version": 1,
            "build": "test-build",
            "information": "public",
            "state": public_state(),
        },
        {
            "seq": 1,
            "event": "decision_source",
            "source": "vanilla",
            "error": "TypeError #1009",
            "stage": "vocabulary",
        },
        {"seq": 2, "event": "pass", "seat": 1},
    ]
    if final:
        records.append(
            {
                "seq": 3,
                "event": "match_end",
                "reason": "final",
                "winner": 0,
                "pending_action": False,
                "state": public_state(),
            }
        )
    return records


def native_settings_text(values: dict[str, str]) -> str:
    """Native framing wraps raw values without escaping their inner JSON quotes."""
    lines = ["[W3GwentPlayTrace]\n"]
    for key, value in values.items():
        rendered = value
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", rendered):
            lines.append(f"{key}={rendered}\n")
        else:
            lines.append(f'{key}="{rendered}"\n')
    return "".join(lines)


def settings_text(records: list[dict], status: str) -> str:
    chunks = []
    for record in records:
        text = json.dumps(record, ensure_ascii=True) + r"\n"
        chunks.extend(text[start : start + 780] for start in range(0, len(text), 780))
    values = {
        "t000000001_status": status,
        "t000000001_build": "test-build",
        "t000000001_count": str(len(chunks)),
    }
    values.update(
        {f"t000000001_{index:09}": chunk for index, chunk in enumerate(chunks)}
    )
    return native_settings_text(values)


class TraceHarvestTests(unittest.TestCase):
    def test_native_raw_chunks_roundtrip_quotes_controls_unicode_and_split_keys(
        self,
    ) -> None:
        records = fixture()
        label = 'quote " slash \\ literal \\n newline\n tab\t CR\r zero\0 é \u0085 \u2028 \u2029 🐺'
        card = {
            "instance_id": 7,
            "template_id": 100,
            "name": label,
            "power": 4,
            "owner": 0,
            "seat": 0,
        }
        records[0]["state"]["players"][0]["rows"]["melee"].append(card)
        before = json.dumps(records[0], ensure_ascii=True)
        padding = (-(before.index('"power"') + 3)) % 780
        card["name"] += "x" * padding
        text = json.dumps(records[0], ensure_ascii=True)
        boundary = text.index('"power"') + 3
        self.assertEqual(boundary % 780, 0)
        chunk_index = boundary // 780
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings"
            raw = settings_text(records, "final")
            raw_chunk = next(
                line
                for line in raw.split("\n")
                if line.startswith(f"t000000001_{chunk_index:09}=")
            )
            self.assertTrue(raw_chunk.split("=", 1)[1].startswith('"wer"'))
            path.write_text(raw)
            parsed = read_traces(path)["t000000001"]
            self.assertEqual(parsed["events"], records)
            self.assertEqual(
                [json.loads(line) for line in parsed["jsonl"].split("\n") if line],
                records,
            )
            # Native chunk delimiters may cut a JSON escape immediately after
            # its backslash. Only concatenation before JSON decoding is safe.
            card["name"] = label
            before = json.dumps(records[0], ensure_ascii=True)
            padding = (-(before.index(r"\u2028") + 1)) % 780
            card["name"] = "x" * padding + label
            encoded = json.dumps(records[0], ensure_ascii=True)
            boundary = encoded.index(r"\u2028") + 1
            self.assertEqual(boundary % 780, 0)
            path.write_text(settings_text(records, "final"))
            self.assertEqual(read_traces(path)["t000000001"]["events"], records)

    def test_native_bare_n_chunk_completes_split_record_delimiter(
        self,
    ) -> None:
        records = fixture(False)
        choice = {"seq": 1, "event": "choice", "seat": 0, "choice": ""}
        choice["choice"] = "x" * (779 - len(json.dumps(choice)))
        self.assertEqual(len(json.dumps(choice)), 779)
        records[1] = choice
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings"
            raw = settings_text(records, "active")
            self.assertTrue(
                any(re.fullmatch(r"t\d+_\d+=n", line) for line in raw.split("\n"))
            )
            path.write_text(raw)
            self.assertEqual(read_traces(path)["t000000001"]["events"], records)

    def test_oversized_and_ambiguous_bare_chunks_are_rejected(self) -> None:
        values = {
            "t000000001_status": "active",
            "t000000001_build": "test-build",
            "t000000001_count": "1",
            "t000000001_000000000": "x" * 781,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings"
            path.write_text(native_settings_text(values))
            with self.assertRaisesRegex(PlayTraceError, "chunk size"):
                read_traces(path)
            for bare in [" n", "n ", "inside whitespace"]:
                values["t000000001_000000000"] = "n"
                raw = native_settings_text(values).replace(
                    "_000000000=n", "_000000000=" + bare
                )
                path.write_text(raw)
                with (
                    self.subTest(bare=bare),
                    self.assertRaisesRegex(PlayTraceError, "whitespace"),
                ):
                    read_traces(path)

    def test_selected_match_ignores_unrelated_malformed_native_framing(self) -> None:
        valid = settings_text(fixture(), "final")
        unrelated = 't000000007_000000001="unterminated\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings"
            path.write_text(valid + unrelated)
            with self.assertRaisesRegex(PlayTraceError, "unterminated"):
                read_traces(path)
            self.assertEqual(
                read_traces(path, match_id="t000000001")["t000000001"]["events"],
                fixture(),
            )

    def test_native_framing_malformed_outer_quotes_separators_and_duplicate_fields_are_rejected(
        self,
    ) -> None:
        valid = settings_text(fixture(False), "active")
        marker = 't000000001_000000000="'
        missing_open = valid.replace(marker, marker[:-1], 1)
        lines = valid.split("\n")
        chunk_line = next(
            index for index, line in enumerate(lines) if line.startswith(marker)
        )
        lines[chunk_line] = lines[chunk_line][:-1]
        missing_close = "\n".join(lines)
        last_record = '{"seq":2,"event":"pass","seat":1}'
        duplicate = '{"seq":2,"seq":2,"event":"pass","seat":1}'

        def replace_last(payload: str) -> str:
            chunks = [
                line for line in valid.split("\n") if re.match(r"t\d+_\d+=", line)
            ]
            return valid.replace(
                chunks[-1], chunks[-1].split("=", 1)[0] + '="' + payload + '"'
            )

        old_json_escaped = json.dumps(last_record + r"\n")[1:-1]
        malformed = [
            missing_open,
            missing_close,
            replace_last(last_record),
            replace_last(duplicate + r"\n"),
            replace_last(last_record + r"\n" + "garbage"),
            replace_last(last_record + "\n"),
            replace_last(old_json_escaped),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings"
            for body in malformed:
                with self.subTest(body=body[-100:]):
                    path.write_text(body)
                    with self.assertRaises(PlayTraceError):
                        read_traces(path)

    def test_live_and_final_snapshots_are_retained_and_timeline_names_the_actual_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / "settings"
            settings.write_text(settings_text(fixture(False), "active"))
            with redirect_stdout(io.StringIO()):
                first = harvest(settings, root / "out", expected_build="test-build")[0]
            first_bytes = first.read_bytes()
            settings.write_text(settings_text(fixture(), "final"))
            with redirect_stdout(io.StringIO()):
                final = harvest(settings, root / "out", expected_build="test-build")[0]
            self.assertNotEqual(first, final)
            self.assertEqual(first.read_bytes(), first_bytes)
            self.assertIn("AI source: vanilla", final.with_suffix(".txt").read_text())
            self.assertIn(
                "vocabulary: TypeError #1009", final.with_suffix(".txt").read_text()
            )
            final.write_text("corrupt archive")
            with self.assertRaisesRegex(PlayTraceError, "overwrite"):
                harvest(settings, root / "out")

    def test_missing_chunks_partial_records_wrong_build_and_event_gaps_are_rejected(
        self,
    ) -> None:
        valid = settings_text(fixture(), "final")
        mutations = [
            valid.replace("t000000001_000000000=", "t000000001_000000099="),
            valid.replace("test-build", "other-build"),
            settings_text([{**fixture()[0], "seq": 1}, *fixture()[1:]], "final"),
            settings_text(fixture(False), "final"),
            '[W3GwentPlayTrace]\nt000000001_build="test-build"\nt000000001_status="active"\nt000000001_count="1"\nt000000001_000000000="unfinished JSON"\n',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings"
            for body in mutations:
                with self.subTest(body=body[:60]):
                    settings.write_text(body)
                    with self.assertRaises(PlayTraceError):
                        read_traces(settings, "test-build")

    def test_hidden_payload_and_unconfirmed_actions_are_refused(self) -> None:
        records = fixture()
        records[0]["state"]["players"][1]["ai_hand"] = [{"template_id": 3}]
        with self.assertRaisesRegex(PlayTraceError, "non-public"):
            validate_events(records, "test-build", "final")
        records = fixture()
        records[1] = {
            "event": "action",
            "seq": 1,
            "seat": 1,
            "action_id": 1,
            "state": public_state(),
            "transaction": None,
        }
        with self.assertRaisesRegex(PlayTraceError, "matching native"):
            validate_events(records, "test-build", "final")

    def test_corrupt_required_fields_and_nonfinite_scores_are_rejected(self) -> None:
        records = fixture()
        del records[2]["seat"]
        with self.assertRaisesRegex(PlayTraceError, "required fields"):
            validate_events(records, "test-build", "final")
        records = fixture()
        records[0]["state"]["players"][0]["score"] = float("inf")
        with self.assertRaisesRegex(PlayTraceError, "invalid score"):
            validate_events(records, "test-build", "final")
        with self.assertRaisesRegex(PlayTraceError, "header"):
            validate_events([None], "test-build", "active")
        for kind in ("pass", "choice", "action_started", "action"):
            records = fixture()
            records[1] = {"seq": 1, "event": kind}
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(PlayTraceError, "required fields"),
            ):
                validate_events(records, "test-build", "final")
        records = fixture(False)
        records.extend(
            [
                {"seq": 3, "event": "action_started", "seat": 1, "action_id": 1},
                {
                    "seq": 4,
                    "event": "action_cancelled",
                    "seat": 0,
                    "action_id": 1,
                    "state": public_state(),
                },
            ]
        )
        with self.assertRaisesRegex(PlayTraceError, "matching native"):
            validate_events(records, "test-build", "active")

    def test_interrupted_and_truncated_traces_remain_partial(self) -> None:
        records = fixture(False)
        for status in ("interrupted", "truncated", "error", "active"):
            validate_events(records, "test-build", status)
            self.assertIn(
                "Partial trace",
                timeline(
                    "t000000001",
                    {"status": status, "build": "test-build", "events": records},
                ),
            )

    def test_selected_current_match_can_be_read_beside_damaged_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / "settings"
            valid = settings_text(fixture(), "final").replace(
                "t000000001", "t000000002"
            )
            settings.write_text(
                valid
                + 't000000001_build="old-build"\nt000000001_status="active"\nt000000001_count="1"\n'
            )
            with self.assertRaisesRegex(PlayTraceError, "chunk gap"):
                read_traces(settings)
            with redirect_stdout(io.StringIO()):
                outputs = harvest(settings, root / "out", "t000000002", "test-build")
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0].parent.name, "t000000002")


if __name__ == "__main__":
    unittest.main()
