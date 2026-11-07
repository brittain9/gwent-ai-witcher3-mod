from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from scripts import extract_game, redbundle

SCREEN = "gameplay\\gui_new\\swf\\gwint\\gwint_game.redswf"


class BundleTests(unittest.TestCase):
    def test_game_reader_returns_every_packed_file_and_the_store_describes_it(
        self,
    ) -> None:
        files = [
            (SCREEN, bytes(range(256)) * 900),
            ("gameplay\\gui_new\\swf\\gwint\\deck_builder.redswf", b"deck" * 17),
            ("gameplay\\items\\def_gwint_cards_final.xml", b"<cards/>"),
        ]
        bundle, store_bytes = redbundle.pack(files)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "blob0.bundle"
            path.write_bytes(bundle)
            entries = extract_game.parse_bundle(path)
            self.assertEqual(
                [entry.name for entry in entries], [name for name, _ in files]
            )
            for entry, (_, data) in zip(entries, files):
                self.assertEqual(extract_game.read_entry(path, entry), data)
                self.assertEqual(entry.offset % 16, 0)
        self.assertEqual(len(bundle) % 16, 0)
        store = redbundle.read_store(store_bytes)
        self.assertEqual(
            store.bundles,
            (("blob0.bundle", len(bundle) - entries[0].offset, entries[0].offset),),
        )
        self.assertEqual(
            [
                (
                    item.path,
                    item.offset,
                    item.packed_size,
                    item.unpacked_size,
                    item.compression,
                )
                for item in store.files
            ],
            [
                (
                    entry.name,
                    entry.offset,
                    entry.packed_length,
                    entry.unpacked_length,
                    1,
                )
                for entry in entries
            ],
        )
        self.assertEqual(
            store.max_packed_size, max(entry.packed_length for entry in entries)
        )
        self.assertEqual(
            store.max_unpacked_size, max(entry.unpacked_length for entry in entries)
        )

    def test_store_hash_table_is_sorted_fnv1a64_of_each_path(self) -> None:
        paths = [f"gameplay\\items\\file_{index:02d}.xml" for index in range(12)]
        _, store_bytes = redbundle.pack([(path, path.encode()) for path in paths])
        hashes_start = len(store_bytes) - 16 * len(paths)
        self.assertEqual(store_bytes[hashes_start - 1], len(paths))
        records = [
            struct.unpack_from("<QIi", store_bytes, hashes_start + 16 * row)
            for row in range(len(paths))
        ]
        self.assertEqual(
            [value for value, _, _ in records],
            sorted(redbundle.fnv1a64(p.encode()) for p in paths),
        )
        self.assertEqual({paths[number - 1] for _, number, _ in records}, set(paths))
        self.assertTrue(all(zero == 0 for _, _, zero in records))

    def test_strict_store_reader_rejects_damage(self) -> None:
        _, store_bytes = redbundle.pack([(SCREEN, b"screen")])
        with self.assertRaisesRegex(redbundle.BundleFormatError, "trailing"):
            redbundle.read_store(store_bytes + b"\0")
        damaged = bytearray(store_bytes)
        damaged[-12] ^= 0xFF  # a hash byte
        with self.assertRaisesRegex(redbundle.BundleFormatError, "hash"):
            redbundle.read_store(bytes(damaged))

    def test_bytes_equal_the_synthetic_reference_layout(self) -> None:
        # Golden output of Better Gwent's format writer for this synthetic input.
        bundle, store = redbundle.pack([(SCREEN, b"golden gwint screen")])
        self.assertEqual(
            hashlib.sha256(bundle).hexdigest(),
            "8b2d200e5efc632be8f85cd7488ca2a896cde17aeff2c62075c0f55116a10004",
        )
        self.assertEqual(
            store.hex(),
            (
                "0356544d070000001b00000013000000570100626c6f62302e62756e646c650067616d65706c61795c6775695f6e6577"
                "5c7377665c6777696e745c6777696e745f67616d652e726564737766000067616d65706c6179006775695f6e65770073"
                "7766006777696e74000200000000000000000000000000000000000000000000000000000000000000000e0000000000"
                "00001b0000001300000001000000000000000000000001000000020000000000000000000000000000000000000000"
                "0000000050010000000000001b000000000000000100000001000000020000000000000000000000000000000000000000"
                "0000000020000000000000005001000001000000010000000100000080053b000000000000003c000000000000004500"
                "0000010000004d000000020000005100000003000000010100000004000000290000000159a1e831e7dd4eff01000000"
                "00000000"
            ),
        )

    def test_pack_refuses_paths_the_game_cannot_index(self) -> None:
        for path in ("Gameplay\\Upper.redswf", "..\\escape.xml", "a" * 300):
            with self.assertRaises(redbundle.BundleFormatError):
                redbundle.pack([(path, b"x")])

    def test_member_names_are_listed_for_any_compression(self) -> None:
        bundle, _ = redbundle.pack([(SCREEN, b"screen")])
        patched = bytearray(bundle)
        struct.pack_into(
            "<I", patched, 32 + 292, 4
        )  # mark the entry LZ4, which the strict reader refuses
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "other.bundle"
            path.write_bytes(bytes(patched))
            self.assertEqual(redbundle.bundle_member_names(path), [SCREEN])


if __name__ == "__main__":
    unittest.main()
