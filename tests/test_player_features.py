from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts import build_mod, package_release
from tests.test_ai_selection import body
from tests.test_mod_package import patched_mini_sources

ROOT = Path(__file__).resolve().parents[1]


class MinimalPlayerFeaturesTests(unittest.TestCase):
    def test_built_player_sources_have_no_assistance_consumers_or_hooks(self):
        patched = patched_mini_sources()
        for source in patched.values():
            for removed in ("GwaiFun", "gwaiFun", "gwaiMeterSuffix", "storyTier"):
                self.assertNotIn(removed, source)
        self.assertFalse(
            any(
                "Fun" in name or "mod_fun" in name
                for name in package_release.SOURCE_FILES
            )
        )
        for name in ("GwaiNetPolicy.as", "GwaiNetWeights.as", "GwaiRunner.as"):
            self.assertIn("mod/ai/as3/" + name, package_release.SOURCE_FILES)

    @unittest.skipUnless(
        shutil.which("node"), "Node is required for controller identity"
    )
    def test_removed_assistance_preserves_default_controller_decisions_and_rng(self):
        # Frozen authored controller methods from the last implementation with all
        # optional assistance disabled. No game source or player positions are used.
        baseline = json.loads(
            (ROOT / "tests/fixtures/controller-default-6103.json").read_text()
        )
        current = {
            "flow": {
                name: body(build_mod.FLOW_METHODS, name) for name in baseline["flow"]
            },
            "ai": {name: body(build_mod.AI_MEMBERS, name) for name in baseline["ai"]},
        }
        for group in ("flow", "ai"):
            baseline[group] = {
                name: re.sub(
                    r"\b(\w+)\s*:\s*(?:Object|Array|String|Boolean|Error|int|Number|CardManager|CardLeaderInstance|CardInstance|CardTransaction)\b(?!\s*\()",
                    r"\1",
                    text,
                )
                for name, text in baseline[group].items()
            }
        harness = r"""
const assert=require('node:assert/strict'), baseline=BASELINE, current=CURRENT;
function exercise(bodies) {
 const rows=[];
 for(const practice of [false,true])for(const enabled of [false,true])for(const signal of [false,true])
 for(const tier of [1,2,3])for(let seed=1;seed<=12;seed++) {
  let rng=seed,draws=0,captures=0,runs=0,fallbacks=0;const decisions=[],messages=[];
  const random=()=>{rng=(Math.imul(rng,1664525)+1013904223)>>>0;draws++;return rng/4294967296};
  const moves=[{kind:'pass'},{kind:'play',instanceId:17,row:0,targetPlayer:1,scoreGain:8},{kind:'leader',instanceId:99}];
  const card={instanceId:17,templateId:'synthetic-unit',listsPlayer:1,inList:5,
   templateRef:{isType:()=>false,hasEffect:()=>false},canBePlacedInSlot:()=>true};
  const manager={getCardInstance:id=>id===17?card:null,getCardLeader:()=>({instanceId:99,canBeUsed:true,
   recalculatePowerPotential(){},getOptimalTransaction:()=>({strategicValue:3,sourceCardInstanceRef:{instanceId:99}})})};
  const identities=Object.fromEntries([1,2,3].map(t=>[t,{CARD_IDS:[],CHECKPOINT_SHA256:String(t).repeat(64),EXPORT_SHA256:String(t+3).repeat(64)}]));
  const env={int:x=>Number(x)|0,getTimer:()=>100,CardManager:{PLAYER_2:1,PLAYER_INVALID:-1,CARD_LIST_LOC_HAND:5,CARD_LIST_LOC_INVALID:-1,getInstance:()=>manager,log(){}},
   CardTemplate:{CardType_Global_Effect:9,CardEffect_Scorch:10},CardTransaction:function(){},TACTIC_NONE:0,TACTIC_PASS:1,TACTIC_MAXIMIZE_WIN:2,
   GwaiFun:{parseSettings:()=>({storyTier:0})},GwaiFunLogic:{tierFor:base=>Math.max(1,base)},
   GwaiNetTierConfig:{dataFor:t=>identities[t]},GwaiNetPolicy:{unknownCards:()=>[],provenance:t=>identities[t].CHECKPOINT_SHA256},
   GwaiCapture:{capture(){captures++;return {policyMode:'network',observation:{ownStrength:7}}},legalMoves:()=>moves},
   GwaiRunner:function(view,legal,selectedTier){runs++;assert.equal(selectedTier,tier);this.winChance=.6;
    this.output=legal[Math.floor(random()*legal.length)];this.advance=()=>true;this.cancel=()=>{};}};
  const params={gwaiConfigureSettings:['data'],gwaiProfileForPlayer:['side'],gwaiNotifyDecision:['profile','fallback','detail'],gwaiReportDecision:['profile','fallback','detail']};
  function attach(target,methods){for(const [name,text]of Object.entries(methods)){
   const fn=new Function('env',...(params[name]||[]),'with(env){'+text+'}');target[name]=(...args)=>fn.call(target,env,...args);}}
  const flow={_gwaiMatchEnded:false,_gwaiSignalShown:false,gwaiMatchEnded(){return this._gwaiMatchEnded},gwaiMeterSuffix:()=>'',
   mcMessageQueue:{PushMessage:m=>messages.push(m)},gwaiRecordDecision(kind,detail){decisions.push({kind,detail,selection:this.gwaiTraceSelection()})}};
  attach(flow,bodies.flow);
  const ai={playerID:1,gameFlowControllerRef:flow,gwaiNativeFallback(){fallbacks++}};attach(ai,bodies.ai);
  flow.gwaiConfigureSettings({version:1,enabled,signal,practice,vanillaDifficulty:tier,practiceDeckDifficulty:tier,practiceDeck:'synthetic-opponent'});
  const began=ai.gwaiBegin();if(began)assert.equal(ai.gwaiPump(),false);
  assert.equal(fallbacks,0);assert.equal(captures,enabled?1:0);assert.equal(runs,enabled?1:0);assert.equal(draws,enabled?1:0);
  rows.push({practice,enabled,signal,tier,seed,began,attitude:ai.attitude,transaction:ai._decidedCardTransaction,
   decisions,messages,rng,draws,captures,runs,fallbacks});
 }
 return JSON.parse(JSON.stringify(rows));
}
const before=exercise(baseline),after=exercise(current);assert.equal(after.length,288);
assert.deepEqual(after,before);
assert.deepEqual(new Set(after.flatMap(r=>r.decisions.map(d=>d.kind))),new Set(['stock','network']));
assert.ok(after.some(r=>r.attitude===1));assert.ok(after.some(r=>r.transaction?.sourceCardInstanceRef?.instanceId===17));
assert.ok(after.some(r=>r.transaction?.sourceCardInstanceRef?.instanceId===99));
""".replace("BASELINE", json.dumps(baseline)).replace("CURRENT", json.dumps(current))
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
