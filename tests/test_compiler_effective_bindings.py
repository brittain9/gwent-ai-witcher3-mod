"""Final native/helper clocks and terminal traits must survive real compilation."""

import unittest


class CompilerEffectiveBindingsTests(unittest.TestCase):
    def test_counts_complete_native_calls_and_each_helper_exactly_once(self):
        from shared_player.compiled_controller import package_function_counts

        helper = "internal class GwaiExample { function f(){ getTimer(); getQualifiedClassName({}); } }"
        sources = {
            "AIPlayerController": "class AIPlayerController { function f(){ getTimer(); } }"
            + helper,
            "GwintGameFlowController": "function start(){ getTimer(); getTimer(); getTimer(); getTimer(); }",
        }
        result = package_function_counts(sources, {"GwaiExample": helper})
        self.assertEqual(
            result["GwintGameFlowController"],
            {"getTimer": 4, "getQualifiedClassName": 0},
        )
        self.assertEqual(
            result["AIPlayerController"], {"getTimer": 1, "getQualifiedClassName": 0}
        )
        self.assertEqual(
            result["GwaiExample"], {"getTimer": 1, "getQualifiedClassName": 1}
        )

    def test_missing_or_duplicate_native_helper_closure_refuses(self):
        from shared_player.compiled_controller import package_function_counts

        helper = "internal class GwaiExample {}"
        for ai in ("class AIPlayerController {}", helper + helper):
            with self.assertRaises(ValueError):
                package_function_counts(
                    {"AIPlayerController": ai}, {"GwaiExample": helper}
                )

    def test_terminal_binding_requires_actual_method_factory_source_and_flag(self):
        from shared_player.compiled_controller import verify_terminal_bindings

        native, helpers = self.fixture()
        result = verify_terminal_bindings(native, helpers, enabled=True)
        self.assertFalse(result["avm2_execution_verified"])
        for target, old, new in [
            (helpers, "terminalPass", "missingPass"),
            (native, "GwaiSharedController.terminalPass(", "Other.terminalPass("),
            (native, '"rule"', '"network"'),
            (helpers, "return true;", "return false;"),
        ]:
            changed = {k: value.replace(old, new) for k, value in target.items()}
            with self.assertRaises(ValueError):
                verify_terminal_bindings(
                    changed if target is native else native,
                    changed if target is helpers else helpers,
                    enabled=True,
                )

    @staticmethod
    def fixture():
        native = {
            "AIPlayerController": "function gwaiBegin(){ var gwaiTerminal:Boolean = GwaiSharedController.terminalPass(view,legal) != null; }",
            "GwintGameFlowController": 'function gwaiNotifyDecision(){ var k = source == "terminal-pass-guard" ? "rule" : (source == "fair-search-v1" ? "search" : "network"); }',
        }
        helpers = {
            "GwaiSharedController": 'public static function terminalPass(o:Object,a:Array):Object { return null; } function decide(){ GwaiSharedController.terminalPass(o,a); sourceKind = "terminal-pass-guard"; modelCall = false; }',
            "GwaiSharedConfig": "public static function terminalPassEnabled():Boolean { return true; }",
        }
        return native, helpers
