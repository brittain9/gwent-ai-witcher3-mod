from __future__ import annotations

import struct
import unittest
import zlib
from pathlib import Path

from scripts.build_probe import (
    AI_DECISION_ANCHOR,
    AI_IMPORT_ANCHOR,
    AI_INJECTION,
    BASE_APPLY_ANCHOR,
    PACKAGE,
    ProbeBuildError,
    _inject_sources,
    _read_chunk_table,
    _verify_compiled_sources,
    normalize_swf,
    patch_redswf,
    require_exactly_once,
    unpack_redswf,
)

ROOT = Path(__file__).resolve().parents[1]


def make_swf(body: bytes, version: int = 10) -> bytes:
    size = 8 + len(body)
    return b"FWS" + bytes((version,)) + struct.pack("<I", size) + body


def make_redswf(swf: bytes, trailer: bytes, compression_level: int = 9) -> bytes:
    header = bytearray(0x28 + 10 * 12)
    chunk_table_offset = len(header)
    chunk_data_offset = chunk_table_offset + 24
    cfx = (
        b"CFX"
        + swf[3:4]
        + struct.pack("<I", len(swf))
        + zlib.compress(swf[8:] + trailer, compression_level)
    )
    cfx_chunk = struct.pack("<I", len(cfx)) + cfx
    chunk = struct.pack("<HHIIIII", 0, 0, 0, len(cfx_chunk), chunk_data_offset, 0, 0)
    chunk_table = chunk
    struct.pack_into(
        "<III", header, 0x28 + 4 * 12, chunk_table_offset, 1, zlib.crc32(chunk_table)
    )
    result = header + chunk_table + cfx_chunk
    struct.pack_into("<II", result, 0x18, len(result), len(result))
    return bytes(result)


class ProbePatchTests(unittest.TestCase):
    def test_exact_hook_rejects_missing_or_ambiguous_anchors(self) -> None:
        self.assertEqual(
            require_exactly_once("before anchor after", "anchor", " inserted", "test"),
            "before anchor inserted after",
        )
        self.assertEqual(
            require_exactly_once(
                "before anchor after", "anchor", " inserted ", "test", before=True
            ),
            "before  inserted anchor after",
        )
        for source in ("missing", "anchor anchor"):
            with self.subTest(source=source), self.assertRaises(ProbeBuildError):
                require_exactly_once(source, "anchor", " injected", "test")

    def test_ai_and_apply_hooks_are_exact_and_use_original_transaction_path(
        self,
    ) -> None:
        ai_source = f"package {PACKAGE}\n{{\n{AI_IMPORT_ANCHOR}\n{AI_DECISION_ANCHOR}\n   }}\n}}"
        patched = _inject_sources(
            ai_source,
            BASE_APPLY_ANCHOR,
            {
                "GwaiProbeChoice": "internal class GwaiProbeChoice {}",
                "GwaiProbeTrace": "internal class GwaiProbeTrace {}",
            },
        )
        self.assertIn(AI_INJECTION, patched["AIPlayerController"])
        self.assertIn(
            "_decidedCardTransaction = gwaiProbeTransaction",
            patched["AIPlayerController"],
        )
        self.assertIn("GwaiProbeTrace.applyStarted", patched["BasePlayerController"])
        self.assertLess(
            patched["AIPlayerController"].index(
                "GwaiProbeChoice.firstSupportedTransaction(this)"
            ),
            patched["AIPlayerController"].index("CalculateCardPowerPotentials();"),
        )
        with self.assertRaisesRegex(ProbeBuildError, "AIPlayerController"):
            _inject_sources(
                AI_DECISION_ANCHOR + AI_DECISION_ANCHOR, BASE_APPLY_ANCHOR, {}
            )
        with self.assertRaisesRegex(ProbeBuildError, "BasePlayerController"):
            _inject_sources(
                ai_source,
                "missing",
                {
                    "GwaiProbeChoice": "internal class GwaiProbeChoice {}",
                    "GwaiProbeTrace": "internal class GwaiProbeTrace {}",
                },
            )

    def test_redswf_roundtrip_preserves_cfx_trailer_and_compiled_swf(self) -> None:
        original_swf = make_swf(b"\x01\x02\x00\x00")
        compiled_swf = make_swf(b"\x03\x04\x05\x00\x00", version=10)
        trailer = b"symbol-table\x00with-tail"
        source = make_redswf(original_swf, trailer)

        self.assertEqual(unpack_redswf(source), (original_swf, trailer))
        patched = patch_redswf(source, compiled_swf, trailer)

        self.assertEqual(unpack_redswf(patched), (compiled_swf, trailer))
        _, table_offset, count = _read_chunk_table(patched)
        self.assertEqual(count, 1)
        chunk = struct.unpack_from("<HHIIIII", patched, table_offset)
        self.assertEqual(chunk[3], len(patched) - chunk[4])

    def test_redswf_ignores_cfx_magic_inside_length_delimited_zlib_payload(
        self,
    ) -> None:
        original_swf = make_swf(b"\x01\x02\x00\x00")
        compiled_swf = make_swf(b"\x03\x04\x05\x00\x00", version=10)
        trailer = b"symbol CFX text after the SWF"
        source = make_redswf(original_swf, trailer, compression_level=0)
        self.assertGreaterEqual(source.count(b"CFX"), 2)
        self.assertEqual(unpack_redswf(source), (original_swf, trailer))

        patched = patch_redswf(source, compiled_swf, trailer)

        self.assertEqual(unpack_redswf(patched), (compiled_swf, trailer))

    def test_normalize_swf_accepts_compressed_output_and_rejects_bad_bytes(
        self,
    ) -> None:
        uncompressed = make_swf(b"\x00\x00")
        compressed = b"CWS" + uncompressed[3:8] + zlib.compress(uncompressed[8:])
        self.assertEqual(normalize_swf(compressed, "test"), uncompressed)
        with self.assertRaises(ProbeBuildError):
            normalize_swf(b"bad", "test")

    def test_authored_probe_limits_choices_and_records_unrounded_numbers(self) -> None:
        choice = (ROOT / "mod/probe/as3/GwaiProbeChoice.as").read_text(encoding="utf-8")
        trace = (ROOT / "mod/probe/as3/GwaiProbeTrace.as").read_text(encoding="utf-8")
        recorder = (ROOT / "mod/probe/recorder/gwai_probe_trace.ws").read_text(
            encoding="utf-8"
        )

        self.assertIn("new CardTransaction()", choice)
        self.assertNotIn("getOptimalTransaction", choice)
        self.assertNotIn("CalculateCardPowerPotentials", choice)
        self.assertNotIn("Math.random", choice)
        self.assertIn("canBePlacedInSlot", choice)
        self.assertIn("targetPlayerID = controller.playerID", choice)
        self.assertIn("targetSlotID = slotID", choice)
        self.assertIn("powerChangeResult = 0", choice)
        self.assertIn("strategicValue = 0", choice)
        self.assertIn(
            "rowOrder:Array = [CardManager.CARD_LIST_LOC_MELEE,CardManager.CARD_LIST_LOC_RANGED,CardManager.CARD_LIST_LOC_SEIGE]",
            choice,
        )
        self.assertIn("CardType_Creature", choice)
        self.assertIn("CardType_Hero", choice)
        self.assertIn("CardType_Spy", choice)
        self.assertIn(
            "template.effectFlags[effectIndex]) != CardTemplate.CardEffect_None", choice
        )
        self.assertIn("if(template.effectFlags != null)", choice)
        self.assertIn("summonFlags.length != 0", choice)
        self.assertIn("return null", choice)
        self.assertIn("transaction !== pendingTransaction", trace)
        self.assertIn("JSON.stringify(record)", trace)
        self.assertIn("power_change:transaction.powerChangeResult", trace)
        self.assertIn("strategic_value:transaction.strategicValue", trace)
        self.assertIn("bytes.writeDouble(value)", trace)
        self.assertIn("power_change_bits", trace)
        self.assertIn("strategic_value_bits", trace)
        self.assertIn('metrics_source:"probe_placeholder"', trace)
        self.assertIn("event OnGwaiProbeTrace", recorder)
        self.assertIn("SetRawConfigValueByStr", recorder)
        self.assertNotIn("handleChoice", choice)
        self.assertNotIn("OnGwaiProbeDecision", recorder)
        self.assertEqual(PACKAGE, "red.game.witcher3.menus.gwint")

    @unittest.skipUnless(
        (
            ROOT / ".local/as3/scripts/red/game/witcher3/menus/gwint/CardTemplate.as"
        ).is_file(),
        "local FFDec source export is not available",
    )
    def test_card_effect_none_is_the_original_empty_effect_sentinel(self) -> None:
        source = (
            ROOT / ".local/as3/scripts/red/game/witcher3/menus/gwint/CardTemplate.as"
        ).read_text(encoding="utf-8")
        self.assertIn("CardEffect_None:int = 0", source)
        self.assertIn(
            "if(this.effectFlags == null || this.effectFlags.length == 0)", source
        )
        self.assertIn("return CardEffect_None;", source)
        self.assertIn("return this.effectFlags[0];", source)
        self.assertIn("if(this.effectFlags[i] == effectFlag)", source)

    def test_compiled_source_guard_rejects_cached_potential_access(self) -> None:
        compiled_ai = "GwaiProbeChoice.firstSupportedTransaction(this); CalculateCardPowerPotentials(); GwaiProbeTrace.selected"
        compiled_base = "GwaiProbeTrace.applyStarted"
        compiled_choice = (
            "CardTransaction canBePlacedInSlot CardTemplate.CardEffect_None "
            "template.effectFlags[effectIndex]) != CardTemplate.CardEffect_None "
            "if(template.effectFlags != null) new CardTransaction() "
            "transaction.sourceCardInstanceRef = card "
            "transaction.targetPlayerID = controller.playerID "
            "transaction.powerChangeResult = 0 transaction.strategicValue = 0 "
            "CARD_LIST_LOC_MELEE"
        )
        compiled_trace = (
            "JSON.stringify ByteArray bytes.writeDouble(value) power_change_bits strategic_value_bits "
            "metrics_source probe_placeholder strategic_value OnGwaiProbeTrace"
        )
        _verify_compiled_sources(
            compiled_ai, compiled_base, compiled_choice, compiled_trace
        )
        with self.assertRaisesRegex(ProbeBuildError, "cached potential"):
            _verify_compiled_sources(
                compiled_ai,
                compiled_base,
                compiled_choice + " card.getOptimalTransaction()",
                compiled_trace,
            )
        with self.assertRaisesRegex(ProbeBuildError, "precede"):
            _verify_compiled_sources(
                "CalculateCardPowerPotentials(); GwaiProbeChoice.firstSupportedTransaction(this); GwaiProbeTrace.selected",
                compiled_base,
                compiled_choice,
                compiled_trace,
            )


if __name__ == "__main__":
    unittest.main()
