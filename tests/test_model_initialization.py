"""Exercise the tier selector with AVM2's uninitialized sibling class slots."""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_mod import (
    TIER_NAMES,
    ModBuildError,
    _tier_config_source,
    verify_compiled_initialization,
)


class CompiledInitializationGateTests(unittest.TestCase):
    def test_missing_or_failed_bytecode_checks_refuse_the_build(self) -> None:
        for code, output in (
            (1, "PASS: 15 helper class initializers"),
            (0, "no check ran"),
        ):
            with (
                self.subTest(code=code, output=output),
                patch(
                    "scripts.build_mod.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        [], code, output.encode("utf-8"), b""
                    ),
                ),
                self.assertRaisesRegex(ModBuildError, "initialization check failed"),
            ):
                verify_compiled_initialization(Path("compiled.swf"), [])

    def test_an_unavailable_or_stalled_checker_refuses_the_build(self) -> None:
        for error in (OSError("no Java"), subprocess.TimeoutExpired("java", 60)):
            with (
                self.subTest(error=error),
                patch("scripts.build_mod.subprocess.run", side_effect=error),
                self.assertRaisesRegex(ModBuildError, "could not run"),
            ):
                verify_compiled_initialization(Path("compiled.swf"), [])


@unittest.skipUnless(
    shutil.which("node"), "Node is required for the tier boundary harness"
)
class ModelInitializationTests(unittest.TestCase):
    def test_models_resolve_after_their_definitions_are_installed(self) -> None:
        classes = dict(zip(TIER_NAMES, ("Easy", "Normal", "Hard")))
        source = _tier_config_source(classes, dict.fromkeys(TIER_NAMES, 0.0))
        source = source.replace("internal class", "class")
        source = re.sub(r"private static const (\w+):Array", r"static \1", source)
        source = re.sub(
            r"public static function (\w+)\(difficulty:int\) : (?:Object|Number)",
            r"static \1(difficulty)",
            source,
        )
        for field in ("MODELS", "MODEL_FOR_DIFFICULTY", "TEMPERATURES"):
            source = source.replace(field + "[", "GwaiNetTierConfig." + field + "[")
        harness = """
const assert = require('node:assert/strict');
// The same-script AVM2 class slots exist, but hold null until initproperty.
let Easy = null, Normal = null, Hard = null;
const int = Math.trunc;
SOURCE
Easy = { CARD_IDS: [11] }; Normal = { CARD_IDS: [22] }; Hard = { CARD_IDS: [33] };
assert.deepEqual([1,2,3].map(tier => GwaiNetTierConfig.dataFor(tier).CARD_IDS),
                 [[11],[22],[33]]);
for (const tier of [0,4]) assert.throws(() => GwaiNetTierConfig.dataFor(tier), /outside 1..3/);
for (const tier of [1,2,3]) assert.equal(GwaiNetTierConfig.temperatureFor(tier), 0);
""".replace("SOURCE", source)
        result = subprocess.run(
            [shutil.which("node"), "-e", harness],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
