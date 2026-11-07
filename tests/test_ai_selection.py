"""Exercise actual flow/controller methods across selection and cancellation."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest

from scripts.build_mod import AI_MEMBERS, FLOW_METHODS, _method_body


def body(source, method):
    return re.sub(
        r"\b(\w+)\s*:\s*(?:Object|Array|String|Boolean|Error|int|Number|CardManager|CardLeaderInstance|CardInstance|CardTransaction)\b(?!\s*\()",
        r"\1",
        _method_body(source, method),
    )


@unittest.skipUnless(
    shutil.which("node"), "Node.js is required for actual-source selection checks"
)
class SelectionTests(unittest.TestCase):
    def test_story_practice_stock_network_tiers_and_cancelled_runner(self):
        flow_methods = {
            name: body(FLOW_METHODS, name)
            for name in [
                "gwaiConfigureSettings",
                "gwaiTier",
                "gwaiProfileForPlayer",
                "gwaiTraceSelection",
                "gwaiNotifyStockTurn",
                "gwaiNotifyDecision",
            ]
        }
        ai_methods = {
            name: body(AI_MEMBERS, name)
            for name in ["gwaiCancel", "gwaiBegin", "gwaiPump", "gwaiReportDecision"]
        }
        harness = r"""
const assert=require('node:assert/strict');
const flowBodies=__FLOW_BODIES__,aiBodies=__AI_BODIES__;
let captures=0,constructs=0,cancels=0,nativeFallbacks=0;
const sources=[],messages=[];
const manager={};
const identities={1:{CHECKPOINT_SHA256:'a'.repeat(64),EXPORT_SHA256:'d'.repeat(64)},
 2:{CHECKPOINT_SHA256:'b'.repeat(64),EXPORT_SHA256:'e'.repeat(64)},
 3:{CHECKPOINT_SHA256:'c'.repeat(64),EXPORT_SHA256:'f'.repeat(64)}};
const env={int:x=>Number(x)|0,getTimer:()=>100,CardManager:{PLAYER_2:1,getInstance:()=>manager,log(){}},
 TACTIC_NONE:0,TACTIC_PASS:1,TACTIC_MAXIMIZE_WIN:2,
 GwaiNetTierConfig:{dataFor:tier=>identities[tier]},
 GwaiNetPolicy:{unknownCards:()=>[],provenance:tier=>identities[tier].CHECKPOINT_SHA256},
 GwaiCapture:{capture(){captures++;return {policyMode:'network',observation:{}}},legalMoves(){return [{kind:'pass'}]}},
 GwaiRunner:function(view,moves,tier){constructs++;this.tier=tier;this.winChance=.6;
   this.output={kind:'pass'};this.cancel=()=>{cancels++};this.advance=()=>true;}
};
const args={gwaiConfigureSettings:['data'],gwaiProfileForPlayer:['side'],
 gwaiNotifyDecision:['profile','fallback','detail'],gwaiReportDecision:['profile','fallback','detail']};
function attach(target,bodies){for(const [name,text]of Object.entries(bodies)){
 const fn=new Function('env',...(args[name]||[]),'with(env){'+text+'}');
 target[name]=(...values)=>fn.call(target,env,...values);
}}
const flow={_gwaiMatchEnded:false,_gwaiSignalShown:false,
 mcMessageQueue:{PushMessage:text=>messages.push(text)},
 gwaiMatchEnded(){return this._gwaiMatchEnded},gwaiMeterSuffix:()=>'',
 gwaiRecordDecision(kind,detail){sources.push({kind,detail,selection:this.gwaiTraceSelection()})}};
attach(flow,flowBodies);
const ai={playerID:1,gameFlowControllerRef:flow,gwaiNativeFallback(){nativeFallbacks++}};
attach(ai,aiBodies);
for(const practice of [false,true]) for(const enabled of [false,true]) for(const tier of [1,2,3]) {
 flow.gwaiConfigureSettings({version:1,enabled,practice,vanillaDifficulty:tier,
  practiceDeckDifficulty:2,practiceDeck:practice?'Lambert':'Baron'});
 assert.equal(flow._gwaiSettings.signal,false,'missing debug setting stays off');
 assert.equal(flow.gwaiProfileForPlayer(0),-1,'human seat never uses this controller');
 const selection=flow.gwaiTraceSelection();
 assert.equal(selection.requested_tier,tier);assert.equal(selection.effective_tier,enabled?tier:0);
 assert.equal(selection.checkpoint_sha256,enabled?identities[tier].CHECKPOINT_SHA256:'');
 const before=captures;
 assert.equal(ai.gwaiBegin(),enabled);
 if(enabled){assert.equal(ai._gwaiRunner.tier,tier);assert.equal(ai.gwaiPump(),false);
   assert.equal(sources.at(-1).kind,'network');assert.equal(ai.attitude,1);
 }else{assert.equal(captures,before,'stock selection does not read/capture network input');
   assert.equal(sources.at(-1).kind,'stock');}
 assert.equal(sources.at(-1).selection.enabled,enabled);
}
assert.equal(messages.length,0,'debug messages stay opt-in');
flow.gwaiConfigureSettings({version:1,enabled:true,vanillaDifficulty:2,practiceDeckDifficulty:2,
 practiceDeck:'Lambert',raiseStory:true});
assert.equal(flow.gwaiTraceSelection().requested_tier,2);assert.equal(flow.gwaiTraceSelection().effective_tier,2);
assert.equal(flow.gwaiTraceSelection().checkpoint_sha256,identities[2].CHECKPOINT_SHA256);
assert.equal(ai.gwaiBegin(),true);const priorCancels=cancels;
flow._gwaiMatchEnded=true;
assert.equal(ai.gwaiPump(),true);assert.equal(cancels,priorCancels+1);
assert.equal(ai._gwaiRunner,null,'ended match cannot release an old move');
flow._gwaiMatchEnded=false;
flow.gwaiConfigureSettings({version:1,enabled:true,vanillaDifficulty:1});
assert.equal(ai.gwaiBegin(),true);
flow.gwaiConfigureSettings({version:1,enabled:false,vanillaDifficulty:1});
assert.equal(ai.gwaiPump(),false);assert.equal(ai._gwaiRunner,null);
assert.equal(nativeFallbacks,1);assert.equal(sources.at(-1).kind,'vanilla');
assert.equal(sources.at(-1).selection.enabled,false);
flow.gwaiConfigureSettings({version:1,enabled:true,vanillaDifficulty:3});
assert.equal(ai.gwaiBegin(),true);assert.equal(ai._gwaiRunner.tier,3);
flow.gwaiConfigureSettings({version:1,enabled:true,vanillaDifficulty:2});
assert.equal(ai.gwaiPump(),false);assert.equal(nativeFallbacks,2);
assert.equal(ai.gwaiBegin(),true);assert.equal(ai._gwaiRunner.tier,2);
assert.equal(ai.gwaiPump(),false);assert.equal(sources.at(-1).kind,'network');
flow.gwaiConfigureSettings({version:9,enabled:true,vanillaDifficulty:3});
assert.equal(flow.gwaiProfileForPlayer(1),-1);assert.equal(flow.gwaiTraceSelection(),null);
console.log('selection matrix passed');
""".replace("__FLOW_BODIES__", json.dumps(flow_methods)).replace(
            "__AI_BODIES__", json.dumps(ai_methods)
        )
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
