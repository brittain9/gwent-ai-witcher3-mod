"""Actual host guard ordering and package source attribution boundaries."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TerminalCompositionTest(unittest.TestCase):
    def test_policy_guard_precedes_owned_draws_and_keeps_original_begin(self):
        from shared_player.terminal_composition import patch_terminal_host

        sources = self.sources()
        result = patch_terminal_host(sources)
        ai = result["AIPlayerController"]
        self.assertLess(
            ai.index("GwaiSharedController.terminalPass("),
            ai.index("gwaiOwnedActionUniforms("),
        )
        self.assertIn("gwaiTerminal ? null :", ai)
        self.assertIn(
            "this._gwaiRunner.begin(this._gwaiView.observation,this._gwaiMoves);", ai
        )
        self.assertIn("this._gwaiRunner.sourceKind", ai)
        self.assertIn(
            'controllerSource == "terminal-pass-guard" ? "rule"',
            result["GwintGameFlowController"],
        )

    def test_existing_sink_explicitly_counts_rule_and_search_with_same_menu(self):
        from shared_player.terminal_composition import patch_settings_asset

        path = (
            Path(__file__).resolve().parents[1]
            / "mod/ai/settings/scripts/local/gwai_settings.ws"
        )
        original = path.read_bytes()
        patched = patch_settings_asset(original).decode()
        for category in ("rule", "search"):
            self.assertIn('"W3GwentAIDiagnostics", "' + category + '", "0"', patched)
            self.assertIn('kind != "' + category + '"', patched)
        self.assertIn("Proven match-winning passes:", patched)
        self.assertIn("Search choices:", patched)
        with self.assertRaises(ValueError):
            patch_settings_asset(patched.encode())

    def test_actual_composed_notification_routes_source_once(self):
        import json
        import subprocess

        from shared_player.host import _body
        from shared_player.terminal_composition import patch_terminal_host

        flow = patch_terminal_host(self.sources())["GwintGameFlowController"]
        start, end = _body(flow, "gwaiNotifyDecision")
        body = (
            flow[start:end]
            .replace("var controllerKind:String", "var controllerKind")
            .replace("var difficulty:String", "var difficulty")
        )
        program = (
            "const assert=require('node:assert/strict'); const notify=new Function('profile','fallback','detail','controllerSource',"
            + json.dumps(body)
            + "); const records=[];const receiver={_gwaiSettings:{signal:false},gwaiRecordDecision:(...a)=>records.push(a)};for(const [source,expected] of [['network','network'],['terminal-pass-guard','rule'],['fair-search-v1','search']]){records.length=0;notify.call(receiver,3,false,'public detail',source);assert.deepEqual(records,[[expected,'public detail']]);}records.length=0;notify.call(receiver,3,true,'failure','terminal-pass-guard');assert.deepEqual(records,[['vanilla','failure']]);"
        )
        result = subprocess.run(
            [
                "node",
                "-e",
                program,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def sources(search=False):
        from shared_player.host_hooks import AI_MEMBERS, FLOW_METHODS

        ai = AI_MEMBERS.replace(
            "new GwaiSharedController(profile)",
            "new GwaiSharedController(profile,this.gameFlowControllerRef.gwaiOwnedActionUniforms(profile,this.playerID,this.gameFlowControllerRef.gwaiPrimaryTicket(this.playerID)))",
        )
        flow = FLOW_METHODS
        if search:
            from shared_player.player_integration import patch_search_factory

            ai += "\nfunction state_begin_ChoseMove() : void { nativePhysics(); }"
            flow += "\nprivate var _gwaiHistory:Object = null;"
            return patch_search_factory(
                {"AIPlayerController": ai, "GwintGameFlowController": flow}
            )
        return {"AIPlayerController": ai, "GwintGameFlowController": flow}
