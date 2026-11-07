from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from w3gwent import catalog

FINAL_XML = b"""<?xml version="1.0"?>
<redxml><definitions><gwint_card_definitions_final>
  <card index="7" name="soldier" power="4" faction_index="F_NORTHERN_KINGDOM">
    <type_flags><flag name="TYPE_CREATURE"/><flag name="TYPE_MELEE"/></type_flags>
    <effect_flags><flag name="EFFECT_NONE"/></effect_flags>
  </card>
  <card index="8" name="summoner" power="1" faction_index="F_NEUTRAL">
    <type_flags><flag name="TYPE_CREATURE"/></type_flags>
    <effect_flags/>
    <summonFlags><flag name="soldier"/></summonFlags>
  </card>
</gwint_card_definitions_final></definitions></redxml>
"""

LEADERS_XML = b"""<?xml version="1.0"?>
<redxml><definitions><gwint_battle_king_card_definitions>
  <card index="1001" name="foltest_copper" power="0" faction_index="F_NORTHERN_KINGDOM">
    <type_flags><flag name="TYPE_KING"/></type_flags>
    <effect_flags><flag name="CP_CLEAR_WEATHER"/></effect_flags>
  </card>
</gwint_battle_king_card_definitions></definitions></redxml>
"""

DECKS_XML = b"""<?xml version="1.0"?>
<redxml><definitions><gwint_decks>
  <deck_collection name="North">
    <deck difficulty="2" leaderCard="foltest_copper" specialCard="">
      <card name="soldier"/><card name="missing_card"/>
    </deck>
    <dynamicCards><card name="gwint_card_dynamic" difficulty="9"/></dynamicCards>
  </deck_collection>
</gwint_decks></definitions></redxml>
"""

CARD_TEMPLATE_AS = """
public static const FactionId_Neutral:int = 0;
public static const FactionId_Northern_Kingdom:int = 3;
public static const CardType_None:uint = 0;
public static const CardType_Melee:uint = 1;
public static const CardType_Creature:uint = 8;
public static const CardType_King:uint = 16384;
public static const CardEffect_None:int = 0;
public static const CardEffect_Clear_Weather:int = 9;
"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bundle(path: Path, entries: dict[str, bytes]) -> None:
    table_size = len(entries) * catalog._extractor().ENTRY_SIZE
    cursor = catalog._extractor().PREAMBLE_SIZE + table_size
    preamble = bytearray(catalog._extractor().PREAMBLE_SIZE)
    preamble[:8] = catalog._extractor().MAGIC
    struct.pack_into("<I", preamble, 16, table_size)
    struct.pack_into("<H", preamble, 20, catalog._extractor().SUPPORTED_VERSION)
    index = bytearray()
    content = bytearray()
    for name, data in entries.items():
        packed = zlib.compress(data)
        name_bytes = name.encode("utf-8")
        record = bytearray(catalog._extractor().ENTRY_SIZE)
        record[: len(name_bytes)] = name_bytes
        struct.pack_into(
            "<IIIIII",
            record,
            272,
            cursor & 0xFFFFFFFF,
            cursor >> 32,
            len(data),
            len(packed),
            zlib.crc32(data) & 0xFFFFFFFF,
            1,
        )
        index.extend(record)
        content.extend(packed)
        cursor += len(packed)
    path.write_bytes(preamble + index + content)


def _make_redswf() -> bytes:
    body = b"fixture-swf\x00\x00"
    declared_length = 8 + len(body)
    return (
        b"CR2W"
        + b"fixture" * 4
        + b"CFX"
        + bytes((10,))
        + struct.pack("<I", declared_length)
        + zlib.compress(body + b"fixture-symbol-table")
    )


def make_sources(
    root: Path,
    *,
    final_xml: bytes = FINAL_XML,
    card_template_source: str = CARD_TEMPLATE_AS,
) -> tuple[Path, Path, Path, Path, Path]:
    game_dir = root / "game"
    source_dir = root / "extracted"
    bundle_dir = game_dir / "content/content0/bundles"
    bundle_dir.mkdir(parents=True)
    (source_dir / "gameplay/items").mkdir(parents=True)
    (source_dir / "gameplay/gui_new/swf/gwint").mkdir(parents=True)

    xml_entries = {
        "gameplay/items/def_gwint_cards_final.xml": final_xml,
        "gameplay/items/def_gwint_battle_king_cards.xml": LEADERS_XML,
        "gameplay/items/def_gwint_decks.xml": DECKS_XML,
    }
    for relative, data in xml_entries.items():
        (source_dir / relative).write_bytes(data)

    redswf = _make_redswf()
    swf = catalog._extractor().extract_cfx_swf(redswf)
    redswf_relative = "gameplay/gui_new/swf/gwint/gwint_game.redswf"
    swf_relative = "gameplay/gui_new/swf/gwint/gwint_game.swf"
    (source_dir / redswf_relative).write_bytes(redswf)
    (source_dir / swf_relative).write_bytes(swf)

    xml_bundle = bundle_dir / "xml.bundle"
    r4gui_bundle = bundle_dir / "r4gui.bundle"
    _write_bundle(xml_bundle, xml_entries)
    _write_bundle(r4gui_bundle, {redswf_relative: redswf})

    def bundle_entry(
        bundle_name: str, relative: str, output: str, data: bytes
    ) -> dict[str, object]:
        asset: dict[str, object] = {
            "source_bundle": bundle_name,
            "source_member": relative,
            "output": output,
            "size": len(data),
            "sha256": _sha256(data),
        }
        if bundle_name == "xml.bundle":
            asset["crc32"] = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
        return asset

    assets = [
        bundle_entry("xml.bundle", rel, rel, data) for rel, data in xml_entries.items()
    ]
    assets.extend(
        [
            bundle_entry("r4gui.bundle", redswf_relative, redswf_relative, redswf),
            {
                "source_bundle": "r4gui.bundle",
                "source_member": redswf_relative,
                "derived_from": redswf_relative,
                "output": swf_relative,
                "format": "FWS",
                "size": len(swf),
                "sha256": _sha256(swf),
            },
        ]
    )

    manifest = {
        "format": catalog.EXTRACTOR_FORMAT,
        "bundle_format": {"magic": "POTATO70", "version": 5},
        "source_game_dir": str(game_dir.resolve()),
        "bundles": {
            name: {
                "source": str((bundle_dir / name).resolve()),
                "size": (bundle_dir / name).stat().st_size,
                "sha256": _sha256((bundle_dir / name).read_bytes()),
                "version": 5,
            }
            for name in ("xml.bundle", "r4gui.bundle")
        },
        "assets": assets,
    }
    (source_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    decompile_dir = root / "as3"
    card_template_relative = "scripts/red/game/witcher3/menus/gwint/CardTemplate.as"
    card_template = decompile_dir / card_template_relative
    card_template.parent.mkdir(parents=True)
    card_template.write_text(card_template_source, encoding="utf-8")
    decompile_manifest = decompile_dir / "manifest.json"
    decompile_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_swf_sha256": _sha256(swf),
                "tool_sha256": _sha256(b"synthetic JPEXS fixture"),
                "files": {card_template_relative: _sha256(card_template.read_bytes())},
            }
        ),
        encoding="utf-8",
    )
    manager_script = root / "gwintManager.ws"
    manager_script.write_text(
        "struct SCardDefinition { typeFlags : int; }", encoding="utf-8"
    )
    return game_dir, source_dir, card_template, manager_script, decompile_manifest


class CatalogTests(unittest.TestCase):
    def test_loads_card_runtime_data_decks_and_per_card_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game_dir, source_dir, card_template, manager_script, decompile_manifest = (
                make_sources(Path(temp))
            )
            result = catalog.load_catalog(
                game_dir,
                source_dir,
                card_template=card_template,
                decompile_manifest=decompile_manifest,
                manager_script=manager_script,
            )

        self.assertEqual(result["schema_version"], 1)
        cards = {card["name"]: card for card in result["cards"]}
        self.assertEqual(cards["soldier"]["types"], 9)
        self.assertEqual(cards["soldier"]["effects"], [0])
        self.assertEqual(cards["soldier"]["raw_flags"]["effects"], ["EFFECT_NONE"])
        self.assertEqual(cards["summoner"]["effects"], [])
        self.assertEqual(
            cards["summoner"]["coverage"]["effect_flags"]["state"], "empty"
        )
        self.assertEqual(cards["summoner"]["summons"], [7])
        self.assertEqual(cards["foltest_copper"]["types"], 0)
        self.assertEqual(cards["foltest_copper"]["raw_flags"]["types"], ["TYPE_KING"])
        self.assertEqual(cards["foltest_copper"]["effects"], [9])
        self.assertTrue(result["coverage"]["all_card_mappings_complete"])
        self.assertEqual(result["coverage"]["cards_with_empty_effects"], 1)
        self.assertEqual(result["coverage"]["cards_with_explicit_none"], 1)

        deck = result["decks"][0]
        self.assertEqual(deck["leader"], 1001)
        self.assertEqual(deck["cards"], [7])
        self.assertEqual(deck["raw_cards"], ["soldier", "missing_card"])
        self.assertEqual(deck["dropped_cards"], ["missing_card"])
        self.assertEqual(
            deck["dynamic_cards"], [{"name": "gwint_card_dynamic", "difficulty": 9}]
        )
        self.assertEqual(result["warnings"][0]["name"], "missing_card")
        self.assertEqual(len(result["source_hashes"]["xml.bundle"]), 64)

    def test_fails_if_extracted_xml_does_not_match_manifest_and_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game_dir, source_dir, card_template, manager_script, decompile_manifest = (
                make_sources(Path(temp))
            )
            extracted = source_dir / "gameplay/items/def_gwint_cards_final.xml"
            extracted.write_bytes(extracted.read_bytes() + b"\n<!-- local edit -->")
            with self.assertRaisesRegex(
                catalog.CatalogError, "differs from its bundled source"
            ):
                catalog.load_catalog(
                    game_dir,
                    source_dir,
                    card_template=card_template,
                    decompile_manifest=decompile_manifest,
                    manager_script=manager_script,
                )

    def test_reports_every_unknown_symbol_in_one_error(self) -> None:
        final_xml = FINAL_XML.replace(
            b'<flag name="TYPE_CREATURE"/>',
            b'<flag name="TYPE_MYSTERY"/>',
            1,
        ).replace(
            b'<flag name="EFFECT_NONE"/>',
            b'<flag name="EFFECT_MYSTERY"/>',
            1,
        )
        with tempfile.TemporaryDirectory() as temp:
            game_dir, source_dir, card_template, manager_script, decompile_manifest = (
                make_sources(Path(temp), final_xml=final_xml)
            )
            with self.assertRaises(catalog.CatalogError) as context:
                catalog.load_catalog(
                    game_dir,
                    source_dir,
                    card_template=card_template,
                    decompile_manifest=decompile_manifest,
                    manager_script=manager_script,
                )
        message = str(context.exception)
        self.assertIn("TYPE_MYSTERY", message)
        self.assertIn("EFFECT_MYSTERY", message)

    def test_unresolved_summon_name_fails_with_card_context(self) -> None:
        final_xml = FINAL_XML.replace(
            b'name="soldier"/></summonFlags>',
            b'name="unknown"/></summonFlags>',
        )
        with tempfile.TemporaryDirectory() as temp:
            game_dir, source_dir, card_template, manager_script, decompile_manifest = (
                make_sources(Path(temp), final_xml=final_xml)
            )
            with self.assertRaisesRegex(
                catalog.CatalogError, "unresolved summon 'unknown'"
            ):
                catalog.load_catalog(
                    game_dir,
                    source_dir,
                    card_template=card_template,
                    decompile_manifest=decompile_manifest,
                    manager_script=manager_script,
                )

    def test_rejects_extraction_manifest_from_another_game_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game_dir, source_dir, card_template, manager_script, decompile_manifest = (
                make_sources(root)
            )
            with self.assertRaisesRegex(
                catalog.CatalogError, "requested game directory"
            ):
                catalog.load_catalog(
                    root / "other-game",
                    source_dir,
                    card_template=card_template,
                    decompile_manifest=decompile_manifest,
                    manager_script=manager_script,
                )

    def test_rejects_decompile_attestation_replayed_for_another_swf(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game_dir, source_dir, card_template, manager_script, decompile_manifest = (
                make_sources(Path(temp))
            )
            attestation = json.loads(decompile_manifest.read_text(encoding="utf-8"))
            attestation["source_swf_sha256"] = "0" * 64
            decompile_manifest.write_text(json.dumps(attestation), encoding="utf-8")
            with self.assertRaisesRegex(
                catalog.CatalogError, "decompile source SWF hash does not match"
            ):
                catalog.load_catalog(
                    game_dir,
                    source_dir,
                    card_template=card_template,
                    decompile_manifest=decompile_manifest,
                    manager_script=manager_script,
                )

    def test_rejects_card_template_modified_after_export_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game_dir, source_dir, card_template, manager_script, decompile_manifest = (
                make_sources(Path(temp))
            )
            card_template.write_text(
                card_template.read_text(encoding="utf-8") + "// edited after export\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                catalog.CatalogError, "does not match the decompile attestation"
            ):
                catalog.load_catalog(
                    game_dir,
                    source_dir,
                    card_template=card_template,
                    decompile_manifest=decompile_manifest,
                    manager_script=manager_script,
                )

    def test_rejects_non_object_or_boolean_version_in_decompile_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            game_dir, source_dir, card_template, manager_script, decompile_manifest = (
                make_sources(Path(temp))
            )
            attestation = json.loads(decompile_manifest.read_text(encoding="utf-8"))
            decompile_manifest.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(catalog.CatalogError, "expected a JSON object"):
                catalog.load_catalog(
                    game_dir,
                    source_dir,
                    card_template=card_template,
                    decompile_manifest=decompile_manifest,
                    manager_script=manager_script,
                )

            attestation["schema_version"] = True
            decompile_manifest.write_text(json.dumps(attestation), encoding="utf-8")
            with self.assertRaisesRegex(
                catalog.CatalogError, "expected decompile manifest schema_version 1"
            ):
                catalog.load_catalog(
                    game_dir,
                    source_dir,
                    card_template=card_template,
                    decompile_manifest=decompile_manifest,
                    manager_script=manager_script,
                )


if __name__ == "__main__":
    unittest.main()
