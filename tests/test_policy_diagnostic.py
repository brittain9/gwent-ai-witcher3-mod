from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build_mod, install_mod, mod_diagnostic, mod_trace
from scripts.harvest_playtrace import PlayTraceError
from scripts.harvest_policy_diagnostic import (
    collect,
    descriptor_from_manifest,
    validate,
)
from tests.test_install_mod import Fixture, make_game, snapshot
from tests.test_mod_package import MINI_AI, MINI_FLOW, MINI_HELPERS, MINI_MENU
from tests.test_playtrace import helper_javascript, js_body

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = "a" * 64


def sources() -> dict[str, str]:
    return {
        "GwaiNetPolicy": (ROOT / "mod/ai/as3/GwaiNetPolicy.as").read_text(),
        "GwaiPolicyCore": (ROOT / "mod/ai/as3/GwaiPolicyCore.as").read_text(),
        "GwaiPlayTrace": mod_trace.helper_source("test-build"),
    }


def diagnostic_javascript() -> str:
    source = mod_diagnostic.HELPER_PATH.read_text()
    body = source[source.index("{") + 1 : source.rfind("}")]
    body = re.sub(r"private static var", "var", body)
    body = re.sub(r"(?:public|private) static function", "function", body)
    body = re.sub(r"\)\s*:\s*(?:void|Object|Array|Boolean|String)\s*\{", ") {", body)
    body = re.sub(
        r"\b(\w+)\s*:\s*(?:Boolean|String|Object|Number|Array|int|Error)\b(?!\s*\()",
        r"\1",
        body,
    )
    body = re.sub(r"catch\((\w+):Error\)", r"catch(\1)", body)
    body = re.sub(r"\bint\s*\(", "Math.trunc(", body)
    return body


def manifest() -> dict:
    model = {
        "checkpoint_sha256": "b" * 64,
        "export_sha256": "c" * 64,
        "observation_profile_id": "faction-unknown-v1",
        "card_count": 1,
        "observation_size": 898,
        "temperature": 0.0,
    }
    return {
        "format": "witcher3-gwent-mod-build-v4",
        "variant": "diagnostic",
        "status": "offline_compiled_and_export_verified",
        "policy_diagnostic": {
            "schema": mod_diagnostic.SCHEMA,
            "build_identity": IDENTITY,
        },
        "tier_policies": {
            tier: copy.deepcopy(model) for tier in ("easy", "normal", "hard")
        },
    }


def fixture() -> list[dict]:
    observation = {
        "seat": 1,
        "initiative": 1,
        "decision_id": 20,
        "round": 1,
        "rounds_played": 0,
        "lives": [2, 2],
        "scores": [40, 36],
        "round_results": [],
        "hand": [],
        "players": [
            {
                "seat": seat,
                "faction": 3,
                "hand_count": 3,
                "deck_count": 19,
                "leader": None,
                "leader_used": True,
                "graveyard": [],
                "rows": {},
                "modifiers": {},
                "passed": False,
            }
            for seat in (0, 1)
        ],
        "weather": [],
        "pending_choice": None,
        "terminal": False,
    }
    legal = [{"id": "pass", "kind": "pass"}]
    return [
        {
            "event": "diagnostic_start",
            "seq": 0,
            "schema": mod_diagnostic.SCHEMA,
            "schema_version": 1,
            "build": "test-build",
            "build_identity": IDENTITY,
            "information": "actor view",
        },
        {
            "event": "policy_input",
            "seq": 1,
            "decision_index": 0,
            "seat": 1,
            "native_decision_id": 20,
            "difficulty": 3,
            "temperature": 0.0,
            "random_draws": [],
            "observation_profile_id": "faction-unknown-v1",
            "checkpoint_sha256": "b" * 64,
            "weights_sha256": "c" * 64,
            "observation": observation,
            "legal_actions": legal,
            "encoded_observation": [0.0] * 897 + [0.002],
            "selected_action": legal[0],
            "selected_action_id": "pass",
            "selected_index": 0,
            "group_logits": [2.0],
            "local_logits": [3.0],
            "action_group_ids": [0],
            "action_features": [[0, 0, 0, 0, 0]],
            "group_features": [[0, 0]],
            "value": 0.6,
            "value_nonfinite": False,
            "outcome_probabilities": [0.2, 0.4, 0.4],
        },
        {"event": "diagnostic_end", "seq": 2, "reason": "final", "decisions": 1},
    ]


class DiagnosticBuildTests(unittest.TestCase):
    def test_generated_diagnostic_captures_after_one_forward_and_preserves_release_sources(
        self,
    ):
        original = sources()
        saved = copy.deepcopy(original)
        generated, metadata = mod_diagnostic.prepare_sources(
            original, "test-build", {"source": "1" * 64}
        )
        self.assertEqual(original, saved)
        self.assertNotIn("GwaiPolicyDiagnostic", original["GwaiNetPolicy"])
        self.assertEqual(
            generated["GwaiNetPolicy"].count("var result:Object = evaluate("), 1
        )
        self.assertEqual(
            generated["GwaiNetPolicy"].count("GwaiPolicyDiagnostic.record("), 1
        )
        body = "".join(
            build_mod._method_body(generated["GwaiNetPolicy"], "decide").split()
        )
        self.assertLess(
            body.index("evaluate("), body.index("GwaiPolicyDiagnostic.record(")
        )
        self.assertLess(
            body.index("GwaiPolicyDiagnostic.record("),
            body.index("returnresult.chosen_action"),
        )
        self.assertEqual(original["GwaiNetPolicy"].count("Math.random()"), 1)
        self.assertEqual(generated["GwaiNetPolicy"].count("Math.random()"), 0)
        self.assertEqual(
            generated["GwaiNetPolicy"].count("GwaiPolicyDiagnostic.random()"), 1
        )
        self.assertEqual(generated["GwaiPolicyDiagnostic"].count("Math.random()"), 1)
        self.assertTrue(metadata["automatic"])
        self.assertTrue(metadata["separate_from_public_trace"])
        self.assertNotIn("__GWAI_DIAGNOSTIC_", generated["GwaiPolicyDiagnostic"])
        mod_diagnostic.verify_compiled(generated)
        damaged = copy.deepcopy(generated)
        damaged["GwaiNetPolicy"] = damaged["GwaiNetPolicy"].replace(
            "GwaiPolicyDiagnostic.record(", "unrelated("
        )
        with self.assertRaises(mod_diagnostic.DiagnosticBuildError):
            mod_diagnostic.verify_compiled(damaged)

    def test_combined_diagnostic_helpers_and_native_trace_hooks_reject_only_real_repatches(
        self,
    ):
        # Authored native stand-ins exercise the same assembly boundary as a build.
        helpers = {**MINI_HELPERS, **sources()}
        for name in (
            "GwaiNetWeights",
            "GwaiNetTierConfig",
            "GwaiNetWeightsData_fixture",
        ):
            helpers[name] = f"internal class {name} {{}}"
        generated, _ = mod_diagnostic.prepare_sources(helpers, "test-build", {})
        extra_flow = """
      public function testTraceAnchors() : void
      {
         this.cardManager.spawnLeaders();
               this.playerControllers[CardManager.PLAYER_2].resetCurrentRoundStatus();
               if(this.playerControllers[this.currentPlayer].currentRoundStatus == BasePlayerController.ROUND_PLAYER_STATUS_DONE) {}
         this.cardManager.updatePlayerLives();
      }
      public function OnEndGameResult() : void {}
      public function handleAcceptMulligan() : void {}
      public function handleDeclineMulligan() : void {}
"""
        flow = MINI_FLOW.replace(
            "      public var gameStarted:Boolean = false;",
            "      public var gameStarted:Boolean = false;" + extra_flow,
        )
        originals = build_mod.patch_sources(MINI_AI, flow, MINI_MENU, generated)
        originals.update(
            {
                "BasePlayerController": """
      protected function state_begin_ApplyingCard() : void {}
      public function testTransactions() : void
      {
         if(!this.cardEffectApplying())
         {
            leaderCard.ApplyLeaderAbility(this.isAI);
            CardManager.getInstance().addCardInstanceIDToList(this._transactionCard.instanceId,slotID,playerID);
         CardManager.getInstance().replaceCardInstanceIDs(this._transactionCard.instanceId,cardInstance.instanceId);
            CardManager.getInstance().sendToGraveyardID(this._transactionCard.instanceId);
         }
         this._currentRoundStatus = value;
      }
""",
                "HumanPlayerController": """
      public function state_update_ApplyingCard() : void
      {
            _turnOver = true;
            _stateMachine.ChangeState("Idle");
      }
""",
                "CardInstance": "public function handleNurseChoice() : void {}",
                "CardLeaderInstance": "\n".join(
                    f"public function {method}() : void {{}}"
                    for method in (
                        "handleHandShowClose",
                        "handleResurrectChoice",
                        "handleCardDrawChoice_Weather",
                        "handleBinChoice",
                        "handlePickChoice",
                    )
                ),
            }
        )
        self.assertIn(
            "GwaiPlayTrace.encodeDiagnostic(", originals["AIPlayerController"]
        )
        patched = mod_trace.patch_sources(originals)
        self.assertEqual(patched["AIPlayerController"], originals["AIPlayerController"])
        mod_trace.verify_compiled(patched, generated["GwaiPlayTrace"])
        with self.assertRaisesRegex(
            mod_trace.TraceBuildError, "hooks already installed"
        ):
            mod_trace.patch_sources(patched)
        for name in {"GwintGameFlowController", *mod_trace.EXTRA_CLASSES}:
            with self.subTest(partially_patched=name):
                partial = {**originals, name: patched[name]}
                with self.assertRaisesRegex(
                    mod_trace.TraceBuildError, "hooks already installed"
                ):
                    mod_trace.patch_sources(partial)

    def test_ambiguous_hook_and_double_instrumentation_fail(self):
        generated, _ = mod_diagnostic.prepare_sources(sources(), "test-build", {})
        with self.assertRaises(mod_diagnostic.DiagnosticBuildError):
            mod_diagnostic.prepare_sources(generated, "test-build", {})
        bad = sources()
        bad["GwaiNetPolicy"] += "\n      _lastWinChance = winChanceOf(result);\n"
        with self.assertRaises(mod_diagnostic.DiagnosticBuildError):
            mod_diagnostic.prepare_sources(bad, "test-build", {})

    def test_diagnostic_variant_has_own_sink_and_incompatible_editions_fail_before_work(
        self,
    ):
        self.assertEqual(
            install_mod.VARIANTS["diagnostic"].mod_name, "modW3GwentAIDiagnostic"
        )
        self.assertEqual(len(install_mod.VARIANTS["ai"].scripts), 2)
        self.assertEqual(len(install_mod.VARIANTS["diagnostic"].scripts), 3)
        for first, second in (("recorder", "diagnostic"), ("probe", "diagnostic")):
            with self.assertRaises(build_mod.ModBuildError):
                build_mod.build(**{first: True, second: True})
        with (
            patch("scripts.install_mod.subprocess.run") as run,
            patch("scripts.install_mod.assemble_package"),
        ):
            run.return_value.returncode = 0
            install_mod.build_package("diagnostic")
            self.assertIn("--diagnostic", run.call_args.args[0])


class DiagnosticPackageTests(Fixture):
    def test_own_sink_installs_in_isolated_folder_and_uninstalls_exactly(self):
        game, pins = make_game(self.root)
        before = snapshot(game)
        package = self.package(pins, "diagnostic")
        install_mod.install_package(game, package_dir=package, runtime_dir=self.runtime)
        scripts = game / "Mods/modW3GwentAIDiagnostic/content/scripts/local"
        self.assertEqual(
            {path.name for path in scripts.iterdir()},
            {"gwai_settings.ws", "gwai_playtrace.ws", "gwai_policy_diagnostic.ws"},
        )
        self.assertNotIn(
            "W3GwentPolicyDiagnostic", (scripts / "gwai_playtrace.ws").read_text()
        )
        self.assertNotIn(
            "W3GwentPlayTrace", (scripts / "gwai_policy_diagnostic.ws").read_text()
        )
        install_mod.uninstall_package(game, runtime_dir=self.runtime)
        self.assertEqual(snapshot(game), before)

    def test_release_and_diagnostic_duplicate_hooks_cannot_coexist(self):
        for variant, existing in (
            ("diagnostic", "modW3GwentAI"),
            ("ai", "modW3GwentAIDiagnostic"),
        ):
            with tempfile.TemporaryDirectory() as directory:
                game, pins = make_game(Path(directory))
                package = self.package(pins, variant)
                (game / "Mods" / existing / "content").mkdir(parents=True)
                before = snapshot(game)
                with self.assertRaisesRegex(
                    install_mod.ModInstallError, "already present"
                ):
                    install_mod.install_package(
                        game, package_dir=package, runtime_dir=self.runtime
                    )
                self.assertEqual(snapshot(game), before)


@unittest.skipUnless(
    shutil.which("node"), "Node required for actual AS3/sink contracts"
)
class DiagnosticRuntimeTests(unittest.TestCase):
    def test_actual_helper_records_exact_inputs_once_and_rejects_hidden_extension(self):
        harness = (
            r"""
const assert=require('node:assert/strict');const sent=[];
const GwaiPlayTrace=(()=>{TRACE;return{encodeDiagnostic:r=>encodeValue(r,0)}})();
function GameEvent(t,n,a){this.name=n;this.args=a}GameEvent.CALL='call';
const red={core:{events:{GameEvent}}};const GwintGameMenu={mSingleton:{dispatchEvent(e){sent.push(e)}}};
DIAGNOSTIC
begin();
const source=FIXTURE[1];const observation=source.observation;const legal=source.legal_actions;
const result={observation_profile_id:source.observation_profile_id,observation:source.encoded_observation,
 chosen_action:legal[0],chosen_action_id:'pass',chosen_index:0,group_logits:[2],local_logits:[3],
 action_group_ids:[0],action_features:source.action_features,group_features:source.group_features,value:.6,outcome_probabilities:[.2,.4,.4]};
record(observation,legal,result,3,{CHECKPOINT_SHA256:'b'.repeat(64),EXPORT_SHA256:'c'.repeat(64)},0);
assert.equal(sent.some(e=>e.name==='OnW3GwentPolicyDiagnosticError'),false,JSON.stringify(sent));
const recordEvent=sent.find(e=>e.name==='OnW3GwentPolicyDiagnosticRecord'&&JSON.parse(e.args[0]).event==='policy_input');
assert.ok(recordEvent,'policy_input must be emitted by the real helper');
const captured=JSON.parse(recordEvent.args[0]);
assert.deepEqual(captured.observation,observation);assert.deepEqual(captured.legal_actions,legal);
assert.deepEqual(captured.encoded_observation,result.observation);assert.equal(captured.selected_action_id,'pass');
observation.hand.push({template_id:999});assert.equal(JSON.parse(recordEvent.args[0]).observation.hand.length,0);
finish('final');assert.equal(sent.at(-1).name,'OnW3GwentPolicyDiagnosticEnd');
const count=sent.length;record(observation,legal,result,3,{},0);assert.equal(sent.length,count);
begin();observation.players[0].hand=['OPPONENT_HIDDEN_SENTINEL'];
record(observation,legal,result,3,{},0);assert.equal(sent.at(-1).name,'OnW3GwentPolicyDiagnosticError');
assert.equal(JSON.stringify(sent).includes('OPPONENT_HIDDEN_SENTINEL'),false);
begin();decisions=512;record({},[],{},3,{},0);assert.equal(sent.at(-1).name,'OnW3GwentPolicyDiagnosticEnd');
assert.equal(sent.at(-1).args[0],'truncated');
""".replace("TRACE", helper_javascript())
            .replace("DIAGNOSTIC", diagnostic_javascript())
            .replace("FIXTURE", json.dumps(fixture()))
        )
        result = subprocess.run(
            ["node"], input=harness, capture_output=True, encoding="utf-8"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_sink_is_automatic_separate_bounded_and_records_failure(self):
        sink = mod_diagnostic.SINK_PATH.read_text()
        bodies = {
            name: js_body(sink, name)
            for name in (
                "GwaiPolicyDiagnosticPad",
                "OnW3GwentPolicyDiagnosticStart",
                "OnW3GwentPolicyDiagnosticRecord",
                "OnW3GwentPolicyDiagnosticEnd",
                "OnW3GwentPolicyDiagnosticError",
            )
        }
        harness = r"""
const assert=require('node:assert/strict'),saved={},snapshots=[],bodies=BODIES;
const cfg={GetRawConfigValueByStr(g,k){assert.equal(g,'W3GwentPolicyDiagnostic');return saved[k]||''},SetRawConfigValueByStr(g,k,v){assert.equal(g,'W3GwentPolicyDiagnostic');saved[k]=String(v)}};
const env={gwaiPolicyDiagnosticActive:false,gwaiPolicyDiagnosticMatchId:0,gwaiPolicyDiagnosticChunkIndex:0,
 theGame:{GetInGameConfigWrapper(){return cfg},SaveUserSettings(){snapshots.push({...saved})}},IntToString:String,
 StringToInt:(s,d)=>Number.isNaN(parseInt(s,10))?d:parseInt(s,10),StrLen:s=>s.length,StrMid:(s,i,n)=>s.substr(i,n),StrLeft:(s,n)=>s.slice(0,n)};
function run(name,args={}){Object.assign(env,args);return new Function('env','with(env){'+bodies[name]+'}')(env)}
env.GwaiPolicyDiagnosticPad=value=>run('GwaiPolicyDiagnosticPad',{value});
assert.equal(run('OnW3GwentPolicyDiagnosticStart',{build:'a'.repeat(64)}),true);
assert.equal(run('OnW3GwentPolicyDiagnosticRecord',{record:'X'.repeat(800)}),true);assert.equal(saved.t000000001_count,'2');
assert.ok(snapshots.length>=2);env.gwaiPolicyDiagnosticChunkIndex=20000;
assert.equal(run('OnW3GwentPolicyDiagnosticRecord',{record:'{}'}),false);assert.equal(saved.t000000001_status,'truncated');
assert.equal(run('OnW3GwentPolicyDiagnosticStart',{build:'a'.repeat(64)}),true);
run('OnW3GwentPolicyDiagnosticError',{message:'capture failed'});assert.equal(saved.t000000002_status,'error');assert.equal(saved.t000000002_error,'capture failed');
""".replace("BODIES", json.dumps(bodies))
        result = subprocess.run(
            ["node"], input=harness, capture_output=True, encoding="utf-8"
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class DiagnosticCollectionTests(unittest.TestCase):
    def test_exact_completed_actor_inputs_survive_native_chunking(self):
        events = fixture()
        body = (
            r"\n".join(json.dumps(event, separators=(",", ":")) for event in events)
            + r"\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "owned.settings"
            chunks = [body[index : index + 780] for index in range(0, len(body), 780)]
            lines = [
                "[W3GwentPolicyDiagnostic]",
                f't000000001_build="{IDENTITY}"',
                't000000001_status="final"',
                f't000000001_count="{len(chunks)}"',
            ] + [
                f't000000001_{index:09d}="{chunk}"'
                for index, chunk in enumerate(chunks)
            ]
            settings.write_text("\n".join(lines) + "\n")
            build_manifest = root / "build-manifest.json"
            build_manifest.write_text(json.dumps(manifest()))
            receipt = collect(settings, "t000000001", build_manifest, root / "out")
            self.assertEqual(receipt["decisions"], 1)
            self.assertTrue(receipt["complete"])
            output = json.loads((root / "out/policy-inputs.json").read_text())
            self.assertEqual(
                output["variants"][0]["observation"], events[1]["observation"]
            )
            self.assertEqual(output["variants"][0]["expected"], events[1])
            canonical = settings.read_text()
            settings.write_text(canonical + 't000000001_0="aliased chunk"\n')
            with self.assertRaisesRegex(PlayTraceError, "canonical"):
                collect(settings, "t000000001", build_manifest, root / "aliased")
            settings.write_text(canonical)
            with self.assertRaises(PlayTraceError):
                wrong = manifest()
                wrong["policy_diagnostic"]["build_identity"] = "d" * 64
                build_manifest.write_text(json.dumps(wrong))
                collect(settings, "t000000001", build_manifest, root / "other")

    def test_missing_selection_identity_profile_and_hidden_fields_are_rejected(self):
        for mutate in (
            lambda row: row.update(random_draws=[0.5]),
            lambda row: row.update(random_draws=[float("nan")]),
            lambda row: row.update(selected_index=1),
            lambda row: row.update(checkpoint_sha256="wrong"),
            lambda row: row.update(checkpoint_sha256="d" * 64),
            lambda row: row.update(weights_sha256="d" * 64),
            lambda row: row.update(observation_profile_id="full-faction-v1"),
            lambda row: row.update(encoded_observation=[0.0]),
            lambda row: row.update(action_group_ids=[1]),
            lambda row: row.update(action_group_ids=[False]),
            lambda row: row.update(action_features=[[0, 0]]),
            lambda row: row.update(group_features=[[0, 0, 0]]),
            lambda row: row.update(outcome_probabilities=[0.5, 0.5]),
            lambda row: row.update(value=0.8),
            lambda row: row.update(value_nonfinite=True),
            lambda row: row.update(observation_profile_id="unknown"),
            lambda row: row["observation"]["players"][0].update(hand=["secret"]),
            lambda row: row.update(encoded_observation=[float("nan")]),
            lambda row: row.update(seq=3),
        ):
            events = fixture()
            mutate(events[1])
            with self.assertRaises(PlayTraceError):
                validate(events, descriptor_from_manifest(manifest()), "final")
        with self.assertRaises(PlayTraceError):
            validate(fixture()[:-1], descriptor_from_manifest(manifest()), "final")
        self.assertEqual(
            len(
                validate(
                    fixture()[:-1], descriptor_from_manifest(manifest()), "interrupted"
                )
            ),
            1,
        )


@unittest.skipUnless(
    shutil.which("node"), "Node required for diagnostic replay integration"
)
class DiagnosticReplayIntegrationTests(unittest.TestCase):
    def test_actual_capture_collect_and_sampled_replay_preserve_choice_and_rng(self):
        import hashlib

        import torch

        from scripts import export_policy_as3
        from tests.synthetic_policy import (
            decision,
            exportable_checkpoint,
            synthetic_suite,
        )

        previous_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        self.addCleanup(torch.set_num_threads, previous_threads)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, catalog = synthetic_suite()
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps(catalog))
            checkpoint = exportable_checkpoint(
                root / "fictional.pt", catalog_path, seed=17
            )
            metadata = export_policy_as3.export(
                checkpoint, catalog_path, root / "model"
            )
            original = sources()
            generated, diagnostic = mod_diagnostic.prepare_sources(
                original, "test-build", {"fictional": "1" * 64}
            )
            network = root / "network.as"
            core = root / "core.as"
            network.write_text(generated["GwaiNetPolicy"])
            core.write_text(generated["GwaiPolicyCore"])
            observation, legal = decision()
            script = r"""
const assert=require('node:assert/strict'),fs=require('node:fs');
const {loadNetworkPolicy}=require(LOADER);const sent=[];
const GwaiPlayTrace=(()=>{TRACE;return{encodeDiagnostic:r=>encodeValue(r,0)}})();
function GameEvent(t,n,a){this.name=n;this.args=a}GameEvent.CALL='call';
const red={core:{events:{GameEvent}}},GwintGameMenu={mSingleton:{dispatchEvent(e){sent.push(e)}}};
const diagnostic=(()=>{DIAGNOSTIC;return{begin,beginDecision,random,record,failedAttempt,finish}})();
const metadata=JSON.parse(fs.readFileSync(METADATA));const bytes=fs.readFileSync(WEIGHTS);
const weights=Array.from({length:metadata.parameter_count},(_,i)=>bytes.readFloatLE(i*4));
const original=ORIGINAL,generated=GENERATED,core=fs.readFileSync(CORE,'utf8'),originalCore=ORIGINAL_CORE;
const observation=OBSERVATION,legal=LEGAL;const identity=[];
diagnostic.begin();
for(const difficulty of [3,1]){
 let nativeDraws=0,releaseDraws=0;const values=[.19,.81];
 Math.random=()=>{assert.ok(nativeDraws<values.length);return values[nativeDraws++]};
 const before=loadNetworkPolicy(original,originalCore,metadata,weights,()=>{assert.ok(releaseDraws<values.length);return values[releaseDraws++]},1);
 const after=loadNetworkPolicy(generated,core,metadata,weights,Math.random,1,false,diagnostic);
 const view=()=>({policyMode:'network',observation:structuredClone(observation),playerID:observation.seat,opponentID:1-observation.seat,scores:observation.scores});
 const bv=view(),av=view();const b=before.decide(bv,structuredClone(legal),difficulty),a=after.decide(av,structuredClone(legal),difficulty);
 assert.notEqual(bv.policyFallback,true);assert.notEqual(av.policyFallback,true);
 assert.equal(after.actionId(a),before.actionId(b));assert.equal(nativeDraws,releaseDraws);
 assert.equal(nativeDraws,difficulty===3?0:2);identity.push({difficulty,draws:nativeDraws,action:after.actionId(a)});
 let neutralDraws=0;const neutral=loadNetworkPolicy(generated,core,metadata,weights,()=>{assert.ok(neutralDraws<values.length);return values[neutralDraws++]},1);
 const nv=view(),n=neutral.decide(nv,structuredClone(legal),difficulty);assert.notEqual(nv.policyFallback,true);
 assert.equal(neutral.actionId(n),before.actionId(b));assert.equal(neutralDraws,releaseDraws);
 const injectedCount=neutralDraws;neutral.resetRandom(123);const firstReset=neutral.decide(view(),structuredClone(legal),difficulty);
 neutral.resetRandom(123);const secondReset=neutral.decide(view(),structuredClone(legal),difficulty);
 assert.equal(neutral.actionId(firstReset),neutral.actionId(secondReset));assert.equal(neutralDraws,injectedCount);
}
diagnostic.finish('final');assert.equal(sent.some(e=>e.name==='OnW3GwentPolicyDiagnosticError'),false,JSON.stringify(sent));
const records=sent.filter(e=>e.name==='OnW3GwentPolicyDiagnosticRecord').map(e=>e.args[0]);
const failureStart=sent.length;diagnostic.begin();
const failurePolicy=loadNetworkPolicy(generated,core,metadata,weights,()=>{throw new Error('Hard must not draw')},1,false,diagnostic);
const goodView={policyMode:'network',observation:structuredClone(observation),playerID:observation.seat,opponentID:1-observation.seat,scores:observation.scores};
failurePolicy.decide(goodView,structuredClone(legal),3);assert.notEqual(goodView.policyFallback,true);
const failedObservation=structuredClone(observation);failedObservation.hand[0].template_id=999999;
const failedView={...goodView,observation:failedObservation};failurePolicy.decide(failedView,structuredClone(legal),3);
assert.equal(failedView.policyFallback,true);const failedEvents=sent.slice(failureStart);
assert.ok(failedEvents.some(e=>e.name==='OnW3GwentPolicyDiagnosticError'));
diagnostic.finish('final');assert.equal(sent.at(-1).name,'OnW3GwentPolicyDiagnosticError');
process.stdout.write(JSON.stringify({records,identity,failedRecords:failedEvents.filter(e=>e.name==='OnW3GwentPolicyDiagnosticRecord').map(e=>e.args[0]),failedObservation}));
"""
            replacements = {
                "LOADER": json.dumps(str(ROOT / "mod/ai/node/replay.js")),
                "TRACE": helper_javascript(),
                "DIAGNOSTIC": diagnostic_javascript()
                .replace("__GWAI_DIAGNOSTIC_ID__", diagnostic["build_identity"])
                .replace("__GWAI_DIAGNOSTIC_BUILD__", "test-build"),
                "METADATA": json.dumps(str(root / "model/metadata.json")),
                "WEIGHTS": json.dumps(str(root / "model/policy.f32.bin")),
                "ORIGINAL": json.dumps(original["GwaiNetPolicy"]),
                "GENERATED": json.dumps(generated["GwaiNetPolicy"]),
                "CORE": json.dumps(str(core)),
                "ORIGINAL_CORE": json.dumps(original["GwaiPolicyCore"]),
                "OBSERVATION": json.dumps(observation),
                "LEGAL": json.dumps(legal),
            }
            # Replace fixed tokens once, before injected authored source can contain them.
            script = re.sub(
                r"\b(?:LOADER|TRACE|DIAGNOSTIC|METADATA|WEIGHTS|ORIGINAL|GENERATED|CORE|ORIGINAL_CORE|OBSERVATION|LEGAL)\b",
                lambda match: replacements[match.group()],
                script,
            )
            captured = subprocess.run(
                ["node"], input=script, capture_output=True, encoding="utf-8"
            )
            self.assertEqual(captured.returncode, 0, captured.stderr)
            payload = json.loads(captured.stdout)
            self.assertEqual([row["draws"] for row in payload["identity"]], [0, 2])
            body = r"\n".join(payload["records"]) + r"\n"
            chunks = [body[index : index + 780] for index in range(0, len(body), 780)]
            build = manifest()
            build["policy_diagnostic"] = diagnostic
            build["policy_network_export_sha256"] = hashlib.sha256(
                network.read_bytes()
            ).hexdigest()
            build["policy_core_export_sha256"] = hashlib.sha256(
                core.read_bytes()
            ).hexdigest()
            for tier, model in build["tier_policies"].items():
                model.update(
                    checkpoint_sha256=metadata["checkpoint_sha256"],
                    export_sha256=metadata["export_sha256"],
                    observation_profile_id=metadata["observation_profile_id"],
                    observation_size=metadata["observation_size"],
                    card_count=metadata["card_count"],
                    temperature=1 if tier == "easy" else 0,
                )
            build_path = root / "build-manifest.json"
            build_path.write_text(json.dumps(build))
            lines = [
                "[W3GwentPolicyDiagnostic]",
                f't000000001_build="{diagnostic["build_identity"]}"',
                't000000001_status="final"',
                f't000000001_count="{len(chunks)}"',
            ] + [
                f't000000001_{index:09d}="{chunk}"'
                for index, chunk in enumerate(chunks)
            ]
            settings = root / "owned.settings"
            settings.write_text("\n".join(lines) + "\n")
            receipt = collect(settings, "t000000001", build_path, root / "collected")
            self.assertEqual(receipt["decisions"], 2)
            inputs = root / "collected/policy-inputs.json"
            original_inputs = json.loads(inputs.read_text())

            failed_body = r"\n".join(payload["failedRecords"]) + r"\n"
            failed_chunks = [
                failed_body[index : index + 780]
                for index in range(0, len(failed_body), 780)
            ]
            failed_settings = root / "failed-owned.settings"
            failed_lines = [
                "[W3GwentPolicyDiagnostic]",
                f't000000002_build="{diagnostic["build_identity"]}"',
                't000000002_status="error"',
                't000000002_error="network policy failed: Error"',
                f't000000002_count="{len(failed_chunks)}"',
            ] + [
                f't000000002_{index:09d}="{chunk}"'
                for index, chunk in enumerate(failed_chunks)
            ]
            failed_settings.write_text("\n".join(failed_lines) + "\n")
            failed_receipt = collect(
                failed_settings, "t000000002", build_path, root / "failed-collected"
            )
            self.assertFalse(failed_receipt["complete"])
            self.assertEqual(failed_receipt["decisions"], 1)
            self.assertEqual(failed_receipt["failed_attempts"], 1)
            failure_document = json.loads(
                (root / "failed-collected/policy-failures.json").read_text()
            )
            self.assertEqual(
                failure_document["failures"][0]["observation"],
                payload["failedObservation"],
            )
            failed_inputs = json.loads(
                (root / "failed-collected/policy-inputs.json").read_text()
            )

            def run_replay(name, data, expected_pass):
                inputs.write_text(json.dumps(data))
                result = subprocess.run(
                    [
                        "node",
                        str(ROOT / "mod/ai/node/diagnostic_replay.js"),
                        "--build-manifest",
                        str(build_path),
                        "--inputs",
                        str(inputs),
                        "--network",
                        str(network),
                        "--core",
                        str(core),
                        "--model",
                        str(root / "model"),
                        "--output",
                        str(root / f"{name}.json"),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    result.returncode == 0, expected_pass, result.stdout + result.stderr
                )
                return json.loads((root / f"{name}.json").read_text())

            run_replay("incomplete-failure", failed_inputs, False)
            replayed = run_replay("valid", original_inputs, True)
            self.assertEqual(replayed["positions"], 2)
            self.assertEqual(
                [case["random_draws_consumed"] for case in replayed["cases"]], [0, 2]
            )
            self.assertTrue(
                all(
                    case["encoded_float32_sha256"] == case["captured_float32_sha256"]
                    for case in replayed["cases"]
                )
            )
            for name, mutate in (
                (
                    "missing-draw",
                    lambda data: data["variants"][1]["expected"]["random_draws"].pop(),
                ),
                (
                    "extra-draw",
                    lambda data: data["variants"][1]["expected"]["random_draws"].append(
                        0.5
                    ),
                ),
                (
                    "hard-extra",
                    lambda data: data["variants"][0]["expected"]["random_draws"].append(
                        0.5
                    ),
                ),
                (
                    "wrong-choice",
                    lambda data: data["variants"][1]["expected"].update(
                        selected_action_id="different"
                    ),
                ),
                (
                    "wrong-profile",
                    lambda data: data["variants"][1]["expected"].update(
                        observation_profile_id="faction-unknown-v1"
                    ),
                ),
                (
                    "wrong-weights",
                    lambda data: data["variants"][1]["expected"].update(
                        weights_sha256="d" * 64
                    ),
                ),
                (
                    "wrong-vector",
                    lambda data: data["variants"][1]["expected"][
                        "encoded_observation"
                    ].pop(),
                ),
            ):
                damaged = copy.deepcopy(original_inputs)
                mutate(damaged)
                run_replay(name, damaged, False)


if __name__ == "__main__":
    unittest.main()
