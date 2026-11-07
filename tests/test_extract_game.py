from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extract_game as extractor


def bundle_entry(
    name: str, payload: bytes, *, compression: int = 1
) -> tuple[str, bytes, int, int]:
    if compression:
        packed = zlib.compress(payload)
    else:
        packed = payload
    return name, packed, len(payload), zlib.crc32(payload) & 0xFFFFFFFF


def write_bundle(
    path: Path, entries: list[tuple[str, bytes, int, int]], *, version: int = 5
) -> None:
    table_size = len(entries) * extractor.ENTRY_SIZE
    cursor = extractor.PREAMBLE_SIZE + table_size
    preamble = bytearray(extractor.PREAMBLE_SIZE)
    preamble[:8] = extractor.MAGIC
    struct.pack_into("<I", preamble, 16, table_size)
    struct.pack_into("<H", preamble, 20, version)
    table = bytearray()
    contents = bytearray()
    for name, packed, unpacked_length, crc32 in entries:
        raw_name = name.encode("utf-8")
        if len(raw_name) >= 256:
            raise ValueError("fixture member name too long")
        record = bytearray(extractor.ENTRY_SIZE)
        record[: len(raw_name)] = raw_name
        struct.pack_into(
            "<IIIIII",
            record,
            272,
            cursor & 0xFFFFFFFF,
            cursor >> 32,
            unpacked_length,
            len(packed),
            crc32,
            1 if len(packed) != unpacked_length else 0,
        )
        table.extend(record)
        contents.extend(packed)
        cursor += len(packed)
    path.write_bytes(preamble + table + contents)


def make_redswf(*, version: int = 10, body: bytes = b"\x00\x00") -> bytes:
    declared_length = 8 + len(body)
    cfx = (
        b"CFX"
        + bytes((version,))
        + struct.pack("<I", declared_length)
        + zlib.compress(body + b"symbol-table")
    )
    return b"CR2W" + b"metadata" * 9 + cfx


class BundleParserTests(unittest.TestCase):
    def test_reads_v5_entry_and_checks_crc(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.bundle"
            entry = bundle_entry("gameplay\\items\\gwint.xml", b"<items/>")
            write_bundle(path, [entry])

            parsed = extractor.parse_bundle(path)
            self.assertEqual([item.name for item in parsed], [entry[0]])
            self.assertEqual(extractor.read_entry(path, parsed[0]), b"<items/>")

            corrupted = bytearray(path.read_bytes())
            corrupted[32 + 288] ^= 1
            path.write_bytes(corrupted)
            parsed = extractor.parse_bundle(path)
            with self.assertRaisesRegex(extractor.ExtractionError, "CRC32 mismatch"):
                extractor.read_entry(path, parsed[0])

    def test_rejects_unsupported_version_and_truncated_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.bundle"
            write_bundle(path, [], version=6)
            with self.assertRaisesRegex(extractor.ExtractionError, "only version 5"):
                extractor.parse_bundle(path)

            preamble = bytearray(extractor.PREAMBLE_SIZE)
            preamble[:8] = extractor.MAGIC
            struct.pack_into("<I", preamble, 16, extractor.ENTRY_SIZE)
            struct.pack_into("<H", preamble, 20, 5)
            path.write_bytes(preamble)
            with self.assertRaisesRegex(
                extractor.ExtractionError, "truncated bundle index"
            ):
                extractor.parse_bundle(path)

    def test_rejects_path_traversal(self) -> None:
        with self.assertRaisesRegex(extractor.ExtractionError, "unsafe bundle path"):
            extractor.safe_member_path("gameplay/../../outside.xml")
        with self.assertRaisesRegex(extractor.ExtractionError, "absolute bundle path"):
            extractor.safe_member_path("C:\\outside.xml")

    def test_rejects_zlib_output_longer_than_index_length(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.bundle"
            packed = zlib.compress(b"this is longer")
            write_bundle(path, [("gwint.xml", packed, 2, 0)])
            parsed = extractor.parse_bundle(path)
            with self.assertRaisesRegex(
                extractor.ExtractionError, "exceeds declared length"
            ):
                extractor.read_entry(path, parsed[0])

    def test_rejects_unreasonable_declared_entry_length(self) -> None:
        entry = extractor.BundleEntry(
            "gwint.xml", 32, extractor.MAX_BUNDLE_ENTRY_SIZE + 1, 1, 0, 1
        )
        with self.assertRaisesRegex(extractor.ExtractionError, "exceeds limit"):
            extractor.read_entry(Path("unused.bundle"), entry)

    def test_rejects_trailing_bytes_in_bundle_zlib_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.bundle"
            data = b"<gwint/>"
            write_bundle(
                path,
                [
                    (
                        "gwint.xml",
                        zlib.compress(data) + b"junk",
                        len(data),
                        zlib.crc32(data),
                    )
                ],
            )
            with self.assertRaisesRegex(extractor.ExtractionError, "trailing bytes"):
                extractor.read_entry(path, extractor.parse_bundle(path)[0])


class CfxTests(unittest.TestCase):
    def test_unwraps_cfx_and_truncates_decompressed_scaleform_table(self) -> None:
        result = extractor.extract_cfx_swf(
            make_redswf(version=11, body=b"movie\x00\x00")
        )
        self.assertEqual(
            result, b"FWS" + bytes((11,)) + struct.pack("<I", 15) + b"movie\x00\x00"
        )

    def test_rejects_invalid_or_unbounded_cfx(self) -> None:
        with self.assertRaisesRegex(extractor.ExtractionError, "End tag"):
            extractor.extract_cfx_swf(make_redswf(body=b"not-a-tag"))

        oversized = b"CR2WCFX\x0a" + struct.pack("<I", extractor.SWF_MAX_SIZE + 1)
        with self.assertRaisesRegex(
            extractor.ExtractionError, "unreasonable SWF length"
        ):
            extractor.extract_cfx_swf(oversized)


class ExtractionTests(unittest.TestCase):
    def test_extracts_selected_gui_and_gwint_xml_with_manifest_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundles = root / "game/content/content0/bundles"
            bundles.mkdir(parents=True)
            game_assets = [
                (
                    name.replace("/", "\\"),
                    *bundle_entry(
                        name.replace("/", "\\"), make_redswf(body=b"gui\x00\x00")
                    )[1:],
                )
                for name in sorted(extractor.GAME_SWFS)
            ]
            write_bundle(bundles / "r4gui.bundle", game_assets)
            xml_name = "gameplay/items/def_gwint_cards_final.xml"
            unrelated_name = "gameplay/items/def_cards.xml"
            write_bundle(
                bundles / "xml.bundle",
                [
                    *[
                        bundle_entry(name.replace("/", "\\"), b"<gwint/>")
                        for name in sorted(extractor.REQUIRED_XML)
                    ],
                    bundle_entry(unrelated_name.replace("/", "\\"), b"<cards/>"),
                ],
            )

            output = root / "out"
            manifest = extractor.extract(root / "game", output)
            extracted_xml = output / xml_name
            extracted_redswf = output / "gameplay/gui_new/swf/gwint/gwint_game.redswf"
            extracted_swf = extracted_redswf.with_suffix(".swf")
            self.assertEqual(extracted_xml.read_bytes(), b"<gwint/>")
            self.assertTrue(extracted_redswf.is_file())
            self.assertEqual(extracted_swf.read_bytes()[:3], b"FWS")
            self.assertFalse((output / unrelated_name).exists())

            manifest_path = output / "manifest.json"
            self.assertEqual(json.loads(manifest_path.read_text()), manifest)
            selected = {asset["output"] for asset in manifest["assets"]}
            self.assertIn(xml_name, selected)
            self.assertIn("gameplay/gui_new/swf/gwint/gwint_game.swf", selected)
            self.assertEqual(
                manifest["bundles"]["xml.bundle"]["sha256"],
                hashlib.sha256((bundles / "xml.bundle").read_bytes()).hexdigest(),
            )

            write_bundle(
                bundles / "xml.bundle", [bundle_entry(unrelated_name, b"<cards/>")]
            )
            missing_output = root / "missing"
            with self.assertRaisesRegex(
                extractor.ExtractionError, "required resources not found"
            ):
                extractor.extract(root / "game", missing_output)
            self.assertFalse((missing_output / "manifest.json").exists())

    def test_output_symlinks_cannot_escape_extraction_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output, outside = root / "output", root / "outside"
            output.mkdir()
            outside.mkdir()
            (output / "gameplay").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(extractor.ExtractionError, "symlink"):
                extractor.output_path_for(
                    output, extractor.safe_member_path("gameplay/gwint.xml")
                )
            (output / "manifest.json").symlink_to(outside / "manifest.json")
            with self.assertRaisesRegex(extractor.ExtractionError, "symlink"):
                extractor.output_path_for(
                    output, extractor.safe_member_path("manifest.json")
                )
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
