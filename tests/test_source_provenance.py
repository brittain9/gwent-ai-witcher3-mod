"""Portable synthetic source attestations retain exact reviewed content pins."""

import hashlib
import json
import struct
import tempfile
import unittest
import zlib
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from scripts import build_probe


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SourceProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.swf = b"FWS\x0a" + struct.pack("<I", 10) + b"\0\0"
        cfx = b"CFX" + self.swf[3:8] + zlib.compress(self.swf[8:])
        header = bytearray(0x28 + 10 * 12)
        table_offset = len(header)
        data_offset = table_offset + 24
        payload = struct.pack("<I", len(cfx)) + cfx
        table = struct.pack("<HHIIIII", 0, 0, 0, len(payload), data_offset, 0, 0)
        struct.pack_into(
            "<III", header, 0x28 + 4 * 12, table_offset, 1, zlib.crc32(table)
        )
        self.redswf = bytes(header) + table + payload
        self.jar = b"synthetic external compiler"

    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        vanilla = root / ".local/vanilla/gameplay/gui_new/swf/gwint"
        vanilla.mkdir(parents=True)
        (vanilla / "gwint_game.swf").write_bytes(self.swf)
        (vanilla / "gwint_game.redswf").write_bytes(self.redswf)
        jar = root / ".local/tools/ffdec/ffdec-cli.jar"
        jar.parent.mkdir(parents=True)
        jar.write_bytes(self.jar)
        extraction = root / ".local/vanilla/manifest.json"
        extraction.write_text(
            json.dumps(
                {
                    "format": "witcher3-gwint-extraction-v1",
                    "source_game_dir": str(root / "installed-game"),
                    "assets": [
                        {"output": relative, "sha256": digest(data)}
                        for relative, data in (
                            (build_probe.SWF_RELATIVE_PATH, self.swf),
                            (build_probe.REDSWF_RELATIVE_PATH, self.redswf),
                        )
                    ],
                }
            )
        )
        decompile = root / ".local/as3/manifest.json"
        files = {}
        for name in ("AIPlayerController", "BasePlayerController"):
            relative = f"scripts/{build_probe.PACKAGE_PATH.as_posix()}/{name}.as"
            path = decompile.parent / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = f"class {name} {{}}".encode()
            path.write_bytes(data)
            files[relative] = digest(data)
        decompile.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_swf_sha256": digest(self.swf),
                    "tool_sha256": digest(self.jar),
                    "files": files,
                }
            )
        )
        return vanilla, jar, extraction

    def verify(self, root: Path) -> tuple:
        with ExitStack() as stack:
            for name, value in {
                "ROOT": root,
                "VANILLA": root / ".local/vanilla/gameplay/gui_new/swf/gwint",
                "VANILLA_MANIFEST": root / ".local/vanilla/manifest.json",
                "AS3_MANIFEST": root / ".local/as3/manifest.json",
                "FFDEC_JAR": root / ".local/tools/ffdec/ffdec-cli.jar",
            }.items():
                stack.enter_context(patch.object(build_probe, name, value))
            stack.enter_context(
                patch.dict(
                    build_probe.PINNED_INPUT_SHA256,
                    {
                        "gwint_game.redswf": digest(self.redswf),
                        "gwint_game.swf": digest(self.swf),
                        "ffdec-cli.jar": digest(self.jar),
                    },
                )
            )
            return build_probe.verify_source_pins()

    def test_relocated_extraction_accepts_same_content_and_records_new_manifest(self):
        manifests = []
        for name in ("first-checkout", "relocated-checkout"):
            root = self.root / name
            _, _, manifest = self.fixture(root)
            result = self.verify(root)
            self.assertEqual(result[:3], (self.redswf, self.swf, self.jar))
            self.assertEqual(result[3], manifest.read_bytes())
            self.assertEqual(result[4], digest(manifest.read_bytes()))
            manifests.append(result[4])
        self.assertNotEqual(*manifests)

    def test_each_reviewed_resource_or_compiler_pin_rejects_changed_bytes(self):
        for index, relative in enumerate(
            (
                ".local/vanilla/gameplay/gui_new/swf/gwint/gwint_game.redswf",
                ".local/vanilla/gameplay/gui_new/swf/gwint/gwint_game.swf",
                ".local/tools/ffdec/ffdec-cli.jar",
            )
        ):
            with self.subTest(relative=relative):
                root = self.root / str(index)
                self.fixture(root)
                path = root / relative
                path.write_bytes(path.read_bytes() + b"tampered")
                with self.assertRaisesRegex(
                    build_probe.ProbeBuildError, "pinned input"
                ):
                    self.verify(root)

    def test_manifest_reference_and_per_class_tampering_still_fail(self):
        for index, (relative, key, message) in enumerate(
            (
                (".local/vanilla/manifest.json", "assets", "does not pin"),
                (".local/as3/manifest.json", "source_swf_sha256", "different SWF"),
                (".local/as3/manifest.json", "tool_sha256", "different FFDec"),
                (".local/as3/manifest.json", "files", "does not match"),
            )
        ):
            with self.subTest(key=key):
                root = self.root / str(index)
                self.fixture(root)
                path = root / relative
                manifest = json.loads(path.read_text())
                manifest[key] = (
                    [] if key == "assets" else {} if key == "files" else "bad"
                )
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(build_probe.ProbeBuildError, message):
                    self.verify(root)
        for name in ("AIPlayerController", "BasePlayerController"):
            with self.subTest(changed_class=name):
                root = self.root / name
                self.fixture(root)
                path = (
                    root
                    / ".local/as3/scripts"
                    / build_probe.PACKAGE_PATH
                    / f"{name}.as"
                )
                path.write_text("changed synthetic class")
                with self.assertRaisesRegex(
                    build_probe.ProbeBuildError, "does not match"
                ):
                    self.verify(root)


if __name__ == "__main__":
    unittest.main()
