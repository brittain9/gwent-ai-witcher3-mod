from __future__ import annotations

import unittest

from scripts.build_mod import ModBuildError, verify_compiled_timer_bindings
from tests.test_mod_package import patched_mini_sources


class TimerBindingTests(unittest.TestCase):
    def test_generated_compilation_unit_imports_and_calls_the_package_function(
        self,
    ) -> None:
        source = patched_mini_sources()["AIPlayerController"]
        self.assertIn("import flash.utils.getTimer;", source)
        self.assertNotIn("flash.utils.getTimer()", source)

    def test_bytecode_rejects_the_live_undefined_flash_lookup(self) -> None:
        broken = 'getlex Multiname("flash",[PackageNamespace("")])\ngetproperty Multiname("utils",[])\ncallproperty Multiname("getTimer",[]), 0'
        with self.assertRaisesRegex(ModBuildError, "undefined variable named flash"):
            verify_compiled_timer_bindings(
                {"AIPlayerController": broken}, {"AIPlayerController": 1}
            )

    def test_bytecode_requires_every_timer_call_to_bind(self) -> None:
        bound = 'findpropstrict QName(PackageNamespace("flash.utils"),"getTimer")\ncallproperty QName(PackageNamespace("flash.utils"),"getTimer"), 0'
        # Qualified class references already compile correctly and remain valid.
        bound += '\ngetlex QName(PackageNamespace("flash.utils"),"Endian")'
        verify_compiled_timer_bindings({"GwaiRunner": bound}, {"GwaiRunner": 1})
        with self.assertRaisesRegex(ModBuildError, "expected 2 bound"):
            verify_compiled_timer_bindings({"GwaiRunner": bound}, {"GwaiRunner": 2})
        with self.assertRaisesRegex(ModBuildError, "got 0"):
            verify_compiled_timer_bindings({}, {"GwaiRunner": 1})
