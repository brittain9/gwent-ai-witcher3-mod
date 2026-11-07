from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts.build_mod import _add_timing_probe_sources

ROOT = Path(__file__).resolve().parents[1]


class TimingProbeSourceTests(unittest.TestCase):
    def test_probe_instruments_only_its_edition_and_preserves_the_action_return(
        self,
    ) -> None:
        original = (ROOT / "mod/ai/as3/GwaiNetPolicy.as").read_text(encoding="utf-8")
        instrumented = _add_timing_probe_sources({"GwaiNetPolicy": original})

        self.assertEqual(
            (ROOT / "mod/ai/as3/GwaiNetPolicy.as").read_text(encoding="utf-8"), original
        )
        self.assertNotIn("GwaiTimingProbe", original)
        self.assertIn(
            "GwaiTimingProbe.measure(observation,legalActions,data,weights,evaluationElapsed)",
            instrumented["GwaiNetPolicy"],
        )
        self.assertLess(
            instrumented["GwaiNetPolicy"].index("GwaiTimingProbe.measure("),
            instrumented["GwaiNetPolicy"].index("return result.chosen_action;"),
        )
        self.assertIn("GwaiNetWeights.load(data)", instrumented["GwaiNetPolicy"])

    def test_probe_buffers_records_and_reports_timer_appropriate_metrics(self) -> None:
        probe = (ROOT / "mod/probe-timing/as3/GwaiTimingProbe.as").read_text(
            encoding="utf-8"
        )
        sink = (
            ROOT / "mod/probe-timing/settings/scripts/local/gwai_timing_probe.ws"
        ).read_text(encoding="utf-8")

        for width in (192, 128, 96, 64):
            self.assertIn(str(width), probe)
            self.assertIn(f'_samples["{width}"]', probe)
        self.assertIn("legal_action_count:legalActions.length", probe)
        self.assertIn('event:"summary"', probe)
        self.assertIn("_records.push(record)", probe)
        self.assertIn("MAX_DECISION_RECORDS:int = 255", probe)
        self.assertIn("student_model_build_ms:_studentModelBuildMs", probe)
        self.assertIn("student_model_warm_ms:_studentModelWarmMs", probe)
        self.assertIn("_discardWidthSample[String(width)]", probe)
        self.assertIn("discarded_width_samples:discardedWidths", probe)
        self.assertIn("mean:sum / sorted.length", probe)
        self.assertIn("samples:sorted.length", probe)
        self.assertIn('"OnW3GwentTimingRecord"', probe)
        self.assertIn('"OnW3GwentTimingBegin"', probe)
        self.assertIn('"OnW3GwentTimingFinish"', probe)
        self.assertIn('"W3GwentTimingProbe"', sink)
        self.assertIn("SetRawConfigValueByStr", sink)
        self.assertEqual(sink.count("theGame.SaveUserSettings();"), 1)
        self.assertNotIn("GetRawConfigValueByStr", sink)
        self.assertNotIn("Math.random", probe)
        replay = (ROOT / "mod/ai/node/replay.js").read_text(encoding="utf-8")
        self.assertIn("mean_ms: mean", replay)

    def test_settings_section_stays_bounded_across_two_simulated_matches(self) -> None:
        probe = (ROOT / "mod/probe-timing/as3/GwaiTimingProbe.as").read_text(
            encoding="utf-8"
        )
        sink = (
            ROOT / "mod/probe-timing/settings/scripts/local/gwai_timing_probe.ws"
        ).read_text(encoding="utf-8")
        cap = int(re.search(r"gwaiTimingProbeIndex >= (\d+)", sink).group(1))
        self.assertIn("gwaiTimingProbeIndex = 0;", sink)
        self.assertIn(
            'SetRawConfigValueByStr( "W3GwentTimingProbe", "count", "0" )', sink
        )
        self.assertIn("MAX_DECISION_RECORDS:int = 255", probe)

        settings: dict[str, str] = {}

        def finish_match(decisions: list[str], summary: str) -> None:
            settings["count"] = "0"
            index = 0
            for decision in decisions[:cap]:
                settings[f"record_{index:06d}"] = decision
                index += 1
            settings[f"record_{index:06d}"] = summary
            index += 1
            settings["count"] = str(index)

        finish_match([f"first-{index}" for index in range(400)], "first-summary")
        first_count = int(settings["count"])
        first_keys = {key for key in settings if key.startswith("record_")}
        finish_match([f"second-{index}" for index in range(7)], "second-summary")

        self.assertEqual(first_count, cap + 1)
        self.assertEqual(int(settings["count"]), 8)
        self.assertLessEqual(
            len({key for key in settings if key.startswith("record_")}), cap + 1
        )
        self.assertEqual(settings["record_000000"], "second-0")
        self.assertEqual(settings["record_000007"], "second-summary")
        self.assertEqual(len(first_keys), cap + 1)

    def test_witcher_script_locals_precede_executable_statements(self) -> None:
        source = (
            ROOT / "mod/probe-timing/settings/scripts/local/gwai_timing_probe.ws"
        ).read_text(encoding="utf-8")
        declarations = re.compile(
            r"(?:function|event)\s+\w+\s*\([^)]*\)\s*(?::\s*\w+\s*)?\{"
        )
        for match in declarations.finditer(source):
            depth = 1
            cursor = match.end()
            while depth:
                if source[cursor] == "{":
                    depth += 1
                elif source[cursor] == "}":
                    depth -= 1
                cursor += 1
            body = source[match.end() : cursor - 1]
            statement_started = False
            for raw_line in body.splitlines():
                line = raw_line.split("//", 1)[0].strip()
                if not line or line in ("{", "}"):
                    continue
                if line.startswith("var "):
                    self.assertFalse(
                        statement_started, f"late local declaration: {line}"
                    )
                else:
                    statement_started = True


if __name__ == "__main__":
    unittest.main()
