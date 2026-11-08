from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import install_mod, settings_menu
from tests.test_dev_practice import javascript_body
from tests.test_install_mod import make_build, make_game

ROOT = Path(__file__).resolve().parents[1]


def assembled_source(variant="ai"):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _, pins = make_game(root)
        package = install_mod.assemble_package(
            variant,
            build_dir=make_build(root, pins, variant),
            package_dir=root / "package",
            pins_path=pins,
        )
        return (
            package
            / "Mods"
            / install_mod.VARIANTS[variant].mod_name
            / "content/scripts/local/gwai_settings.ws"
        ).read_text()


class SettingsCompilerTests(unittest.TestCase):
    def test_player_build_exposes_only_the_six_play_and_troubleshooting_controls(self):
        source = assembled_source()
        controls = javascript_body("GwaiMenuControls", source)
        self.assertEqual(
            [
                name
                for name in (
                    "GwaiEnabled",
                    "GwaiDevOpponent",
                    "GwaiDevStart",
                    "GwaiDevReport",
                    "GwaiSignal",
                    "GwaiTrace",
                )
                if f"'{name}'" in controls
            ],
            [
                "GwaiEnabled",
                "GwaiDevOpponent",
                "GwaiDevStart",
                "GwaiDevReport",
                "GwaiSignal",
                "GwaiTrace",
            ],
        )
        self.assertEqual(controls.count("entries.PushBackFlashObject(option);"), 6)
        for feature in (
            "Coach",
            "Story",
            "Lab",
            "Meter",
            "PostGame",
            "Blunder",
            "Stats",
        ):
            self.assertNotIn("Gwai" + feature, source)

    def test_unsupported_menu_semantics_cannot_silently_disappear(self):
        menu = (ROOT / install_mod.VARIANTS["ai"].menu).read_text()
        changes = [
            ('displayType="TOGGLE"', 'displayType="SLIDER"'),
            ('id="GwaiEnabled"', 'id="unsafe\'name"'),
            ('tags="nonLocalizedName"', 'tags="nonLocalizedName;buffered"'),
            ('varId="GwaiDevOpponent" value="1"', 'varId="GwaiDevOpponent" value="99"'),
            ('<Option id="1"', '<Option id="7"'),
        ]
        for old, new in changes:
            with self.subTest(change=new), self.assertRaises(ValueError):
                settings_menu.assemble_settings(
                    settings_menu.MARKER, menu.replace(old, new)
                )
        for source in ("", settings_menu.MARKER * 2):
            with self.assertRaises(ValueError):
                settings_menu.assemble_settings(source, menu)


@unittest.skipUnless(shutil.which("node"), "Node is required for the settings boundary")
class SelfContainedMenuTests(unittest.TestCase):
    def test_actual_assembled_controls_route_and_persist_without_a_native_group(self):
        source = assembled_source()
        names = (
            "GwaiMenuGroupId",
            "GwaiMenuControl",
            "GwaiMenuControls",
            "GwaiMenuAppend",
            "GwaiMenuAcceptsValue",
            "OnOptionValueChanged",
            "OnCancelOptionValueChange",
            "showOptionsPanel",
        )
        bodies = {name: javascript_body(name, source) for name in names}
        xml = ET.parse(ROOT / install_mod.VARIANTS["ai"].menu)
        expected = [
            {
                "id": v.get("id"),
                "label": v.get("displayName"),
                "kind": v.get("displayType"),
                "indent": "indent" in v.get("tags", "").split(";"),
                "choices": [
                    o.get("displayName") for o in v.findall("OptionsArray/Option")
                ],
            }
            for v in xml.findall("Group/VisibleVars/Var")
        ]
        harness = r"""
const assert=require('node:assert/strict'), bodies=BODIES, expected=EXPECTED;
function array(items=[]) {return {items, GetLength(){return items.length}, GetElementFlashObject(i){return items[i]},
 PushBackFlashObject(v){items.push(v)},PushBackFlashString(v){items.push(v)}}}
function object(values={}) {const obj={values};for(const type of ['String','Int','UInt','Bool','Array']){
 obj['SetMemberFlash'+type]=(k,v)=>{values[k]=v};obj['GetMemberFlash'+type]=k=>values[k];}return obj;}
const sentinel=31415, values={GwaiSettingsVersion:'3',GwaiEnabled:'true',GwaiDevOpponent:'38'};
let saves=0, nativeCalls=0, publishes=0, initializeCalls=0;
const cfg={GetRawConfigValueByStr(g,k){assert.equal(g,'W3GwentAI');return values[k]??''},
 SetRawConfigValueByStr(g,k,v){assert.equal(g,'W3GwentAI');values[k]=v},
 GetGroupName(){throw new Error('no native XML group exists')},GetGroupIdx(){return -1},
 GetGroupsNum(){return 0},GetModsSubCategoryCount(){return 0}};
const kinds={TOGGLE:1,OPTIONS:2,BUTTON:3,SEPARATOR:4};
const env={isMainMenu:true,hasChangedOption:false,NameToString:String,IntToString:String,
 NameToFlashUInt:n=>n==='W3GwentAISettings'?sentinel:n,
 IGMActionType_Toggle:1,IGMActionType_List:2,IGMActionType_Button:3,IGMActionType_Separator:4,
 IGMActionType_MenuHolder:5,IGMActionType_MenuLastHolder:6,
 GetLocStringByKeyExt:k=>k==='panel_mainmenu_mods'?'Mods':k,
 IngameMenu_AddGwentDifficultyOption(storage,rows){rows.PushBackFlashObject(object({id:'GwentDifficulty',tag:'GwentDifficulty',
 groupID:'Gameplay',current:'1',startingValue:'1',label:'native difficulty',type:2,subElements:array(['E','N','H'])}))},
 GwaiSettingsInitialize(){initializeCalls++},OnPlaySoundEvent(){},
 theGame:{GetInGameConfigWrapper:()=>cfg,SaveUserSettings(){saves++}},
 wrappedMethod(){nativeCalls++;return 'native-result'},
 m_flashValueStorage:{CreateTempFlashObject:()=>object(),CreateTempFlashArray:()=>array(),
 SetFlashArray(k,v){assert.equal(k,'ingamemenu.options.entries');env.published=v;publishes++}}};
const params={GwaiMenuGroupId:[],GwaiMenuControl:['storage','optionName','label','kind','indent'],
 GwaiMenuControls:['storage'],GwaiMenuAppend:['storage','entries','isMainMenu'],GwaiMenuAcceptsValue:['optionName','value'],
 OnOptionValueChanged:['groupId','optionName','optionValue'],OnCancelOptionValueChange:['groupId','optionName'],showOptionsPanel:[]};
for(const name of Object.keys(params))env[name]=(...args)=>new Function('env',...params[name],'with(env){'+bodies[name]+'}')(env,...args);
const unrelated=object({id:'other_mod',label:'Another mod',subElements:array([object({id:'OtherEnabled'})])});
for(const hasMods of [false,true])for(const isMainMenu of [false,true]){
 env.isMainMenu=isMainMenu;
 const originalMods=object({id:'mainmenu_mods',label:'Mods',subElements:array([unrelated])});
 env.IngameMenu_FillOptionsSubMenuData=()=>array(hasMods?[originalMods]:[]);
 const before=saves;env.showOptionsPanel();assert.equal(saves,before,'opening does not save/enable preferences');
 const mods=env.published.items.find(o=>o.values.id==='mainmenu_mods');assert.ok(mods);
 if(hasMods){assert.equal(mods,originalMods);assert.equal(mods.values.subElements.items[0],unrelated)}
 const page=mods.values.subElements.items.find(o=>o.values.id==='gwai_trained_opponents');
 assert.equal(page.values.label,'Gwent: Trained Opponents');assert.equal(page.values.type,6);
 const rows=page.values.subElements.items,ownRows=rows.filter(o=>o.values.id!=='GwentDifficulty');
 assert.equal(rows.length,isMainMenu?6:7);assert.equal(rows.filter(o=>o.values.id==='GwentDifficulty').length,isMainMenu?0:1);
 assert.deepEqual(ownRows.map(o=>o.values.id),expected.map(o=>o.id));
 for(let i=0;i<ownRows.length;i++){
  const row=ownRows[i].values,spec=expected[i];assert.equal(row.label,spec.label);assert.equal(row.type,kinds[spec.kind]);
  assert.equal(row.indent,spec.indent);assert.equal(row.groupID,sentinel);assert.equal(row.tag,spec.id);
  assert.deepEqual(row.subElements.items,spec.choices);assert.equal(row.disabled,false);
 }
 assert.equal(rows.find(o=>o.values.id==='GwaiDevOpponent').values.current,'38');
 if(!isMainMenu){const native=rows.find(o=>o.values.id==='GwentDifficulty').values;
  assert.equal(native.groupID,'Gameplay');assert.equal(native.tag,'GwentDifficulty');assert.equal(native.current,'1');
  assert.deepEqual(native.subElements.items,['E','N','H']);}
 assert.equal(values.GwaiMenuTransport,'script');assert.equal(values.GwaiMenuVarCount,isMainMenu?'6':'7');
}
assert.equal(initializeCalls,4);assert.equal(publishes,4);assert.equal(nativeCalls,4);
for(const spec of expected){
 if(spec.kind==='TOGGLE')for(const v of ['true','false']){
  const beforeNative=nativeCalls,beforeSave=saves;
  assert.equal(env.OnOptionValueChanged(sentinel,spec.id,v),true);
  assert.equal(values[spec.id],v);assert.equal(saves,beforeSave+1);assert.equal(nativeCalls,beforeNative);
 }
 if(spec.kind==='OPTIONS')for(let i=0;i<spec.choices.length;i++){
  const before=saves;env.OnOptionValueChanged(sentinel,spec.id,String(i));assert.equal(values[spec.id],String(i));assert.equal(saves,before+1);
 }
}
const before=JSON.stringify(values), beforeSave=saves;
for(const [name,value] of [['GwaiEnabled','1'],['GwaiDevTier','2'],['GwaiDevOpponent','1extra'],
 ['GwaiDevOpponent','39'],['GwaiDevOpponent','-1'],['GwaiDevStart','true'],['GwaiDevReport','true'],['OtherSetting','true']])
 assert.equal(env.OnOptionValueChanged(sentinel,name,value),true);
assert.equal(JSON.stringify(values),before);assert.equal(saves,beforeSave);
assert.equal(env.OnCancelOptionValueChange(sentinel,'GwaiDevOpponent'),true);
assert.equal(env.OnCancelOptionValueChange(sentinel,'UnknownControl'),true);
assert.equal(env.OnOptionValueChanged(7,'OtherSetting','true'),'native-result');
assert.equal(env.OnCancelOptionValueChange(7,'OtherSetting'),'native-result');
// Reopening reconstructs controls from the persisted raw values, including the last list positions.
env.showOptionsPanel();const rows=env.published.items.find(o=>o.values.id==='mainmenu_mods').values.subElements.items
 .find(o=>o.values.id==='gwai_trained_opponents').values.subElements.items;
assert.equal(rows.find(o=>o.values.id==='GwaiDevOpponent').values.current,'38');
assert.ok(!rows.some(o=>o.values.id==='GwaiDevTier'));
assert.equal(env.hasChangedOption,true);
""".replace("BODIES", json.dumps(bodies)).replace("EXPECTED", json.dumps(expected))
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_difficulty_refreshes_both_pages_in_one_options_session(self):
        source = assembled_source()
        bodies = {
            name: javascript_body(name, source)
            for name in (
                "OnOptionValueChanged",
                "OnShowOptionSubmenu",
                "GwaiGwentDifficulty",
            )
        }
        harness = r"""
const assert=require('node:assert/strict'),bodies=BODIES;
const gameplayGroup=71,difficultyTag=91,ownGroup=31415;
function fixture(initial){
 const pages={gameplay:{current:String(Math.max(initial-1,0))},gwai_trained_opponents:{current:String(Math.max(initial-1,0))}};
 const events=[],state={fact:initial,active:'gameplay',updates:null,refreshes:0,nativeChanges:0};
 const env={isMainMenu:false,GwaiMenuGroupId:()=>ownGroup,
  FactsQueryLatestValue(key){assert.equal(key,'gwent_difficulty');return state.fact},
  theGame:{GetInGameConfigWrapper(){throw new Error('difficulty must not read or write a mod preference')},
   SaveUserSettings(){throw new Error('native fact saving must stay on the native path')},
   GetGuiManager:()=>({ForceProcessFlashStorage(){
    events.push('refresh');state.refreshes++;
    assert.ok(state.updates);for(const row of state.updates.items){
     assert.equal(row.tag,difficultyTag);
     // Only the visible copy is refreshed. Hidden cached rows deliberately stay stale.
     pages[state.active].current=row.current;
    }state.updates=null;
   }})},
  m_flashValueStorage:{CreateTempFlashArray(){return {items:[],PushBackFlashObject(row){this.items.push(row)}}},
   SetFlashArray(key,rows){assert.equal(key,'options.force_update_values');state.updates=rows}},
  IngameMenu_AddGwentDifficultyOption(storage,rows){
   events.push('native-helper');rows.PushBackFlashObject({tag:difficultyTag,current:String(Math.max(state.fact-1,0))});
  }};
 const params={OnOptionValueChanged:['groupId','optionName','optionValue'],
  OnShowOptionSubmenu:['actionType','menuTag','id'],GwaiGwentDifficulty:[]};
 const handlers={OnOptionValueChanged(group,name,value){
   events.push('native-change');assert.equal(group,gameplayGroup);assert.equal(name,'GwentDifficulty');
   if(['0','1','2'].includes(value)){state.fact=Number(value)+1;state.nativeChanges++}
   return 'native-result';
  },OnShowOptionSubmenu(actionType,menuTag,id){events.push('native-navigation');assert.equal(id,state.active)}};
 function run(name,...args){env.wrappedMethod=(...nativeArgs)=>handlers[name](...nativeArgs);
  return new Function('env',...params[name],'with(env){'+bodies[name]+'}')(env,...args)}
 return {pages,events,state,env,run};
}
for(const initial of [0,1,2,3])for(const first of ['gameplay','gwai_trained_opponents']){
 const f=fixture(initial),second=first==='gameplay'?'gwai_trained_opponents':'gameplay';
 for(const [page,choice] of [[first,'2'],[second,'0'],[first,'1'],[second,'2']]){
  f.state.active=page;f.events.length=0;
  f.run('OnShowOptionSubmenu',6,17,page);
  assert.deepEqual(f.events,['native-navigation','native-helper','refresh']);
  assert.equal(f.pages[page].current,String(Math.max(f.state.fact-1,0)));
  const hidden=page===first?second:first,beforeHidden=f.pages[hidden].current;
  // The UI commits its visible row, then the unmodified native handler sets the one shared fact.
  f.pages[page].current=choice;
  assert.equal(f.run('OnOptionValueChanged',gameplayGroup,'GwentDifficulty',choice),'native-result');
  assert.equal(f.state.fact,Number(choice)+1);assert.equal(f.run('GwaiGwentDifficulty'),Number(choice)+1);
  assert.equal(f.pages[hidden].current,beforeHidden,'no assumption that hidden cached copies update');
  f.state.active=hidden;f.run('OnShowOptionSubmenu',6,17,hidden);
  assert.equal(f.pages[hidden].current,choice,'reentering the other page must show the shared value');
 }
 assert.equal(f.state.nativeChanges,4);
 const refreshes=f.state.refreshes;
 for(const [main,id] of [[false,'audio'],[true,'gameplay'],[true,'gwai_trained_opponents']]){
  f.env.isMainMenu=main;f.state.active=id;f.events.length=0;f.run('OnShowOptionSubmenu',6,17,id);
  assert.deepEqual(f.events,['native-navigation']);assert.equal(f.state.refreshes,refreshes);
 }
}
""".replace("BODIES", json.dumps(bodies))
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_probe_package_also_contains_all_controls_in_the_actual_script(self):
        source = assembled_source("probe")
        self.assertNotIn(settings_menu.MARKER, source)
        self.assertIn('"Practice opponent / deck"', source)
        self.assertIn('"Show AI status (current / latest match)"', source)
        self.assertNotIn("GetGroupIdx", source)
        self.assertNotIn("GetVarsNumByGroupName", source)
        self.assertNotIn("GetGroupDisplayName", source)
