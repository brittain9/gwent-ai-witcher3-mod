import hashlib
import json
import locale
import struct
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import decompile_game
from decompile_game import decompile


class WindowsManifestReferences(type(Path())):
    def relative_to(self, *args, **kwargs):
        return PureWindowsPath(super().relative_to(*args, **kwargs))


FAKE_JAVA = """
from pathlib import Path
import sys
root = Path(sys.argv[-2]) / "scripts/red/game/witcher3/menus/gwint"
root.mkdir(parents=True)
for name in ("CardTemplate", "CardManager", "CardInstance", "CardEffectManager",
             "GwintGameFlowController", "AIPlayerController", "CardLeaderInstance",
             "BasePlayerController"):
    (root / (name + ".as")).write_text("class " + name + " {}")
if "corrupt" in sys.argv[0]:
    path = Path(sys.argv[-1]); path.write_bytes(path.read_bytes() + b"changed")
sys.stdout.buffer.write('FFDec path: été 日本語'.encode('utf-8'))
sys.stderr.buffer.write('FFDec diagnostics: été 日本語'.encode('utf-8'))
if 'bad_encoding' in sys.argv[0]: sys.stdout.buffer.write(bytes([0xe9]))
"""


class DecompileArtifactTests(unittest.TestCase):
    def test_invalid_tool_encoding_retains_bytes_and_cannot_attest_an_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, jar, fake = (
                root / "game.swf",
                root / "tool.jar",
                root / "bad_encoding.py",
            )
            source.write_bytes(b"FWS\x0c" + struct.pack("<I", 10) + b"\0\0")
            jar.write_bytes(b"synthetic tool")
            fake.write_text(FAKE_JAVA, encoding="utf-8")
            output = root / "as3"
            with self.assertRaisesRegex(
                ValueError, r"not UTF-8; original bytes:.*\\xe9"
            ):
                decompile(source, jar, output, java=[sys.executable, str(fake)])
            self.assertFalse((output / "manifest.json").exists())

    def test_export_manifest_binds_source_and_every_exported_class(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, jar, fake = root / "game.swf", root / "tool.jar", root / "java.py"
            source.write_bytes(b"FWS\x0c" + struct.pack("<I", 10) + b"\0\0")
            jar.write_bytes(b"synthetic tool")
            fake.write_text(FAKE_JAVA, encoding="utf-8")
            output = root / "as3"
            with (
                patch.object(decompile_game, "Path", WindowsManifestReferences),
                patch.object(locale, "getencoding", return_value="cp1252"),
            ):
                manifest = decompile(
                    source, jar, output, java=[sys.executable, str(fake)]
                )
            self.assertEqual(
                manifest["source_swf_sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                json.loads((output / "manifest.json").read_text()), manifest
            )
            for relative, expected in manifest["files"].items():
                self.assertNotIn("\\", relative)
                self.assertEqual(
                    hashlib.sha256((output / relative).read_bytes()).hexdigest(),
                    expected,
                )
            log = (output / "decompile.log").read_text(encoding="utf-8")
            self.assertIn("FFDec path: été 日本語", log)
            self.assertIn("FFDec diagnostics: été 日本語", log)

    def test_changed_input_cannot_receive_a_success_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, jar, fake = (
                root / "game.swf",
                root / "tool.jar",
                root / "corrupt.py",
            )
            source.write_bytes(b"FWS\x0c" + struct.pack("<I", 10) + b"\0\0")
            jar.write_bytes(b"synthetic tool")
            fake.write_text(FAKE_JAVA, encoding="utf-8")
            output = root / "as3"
            with self.assertRaisesRegex(ValueError, "changed during export"):
                decompile(source, jar, output, java=[sys.executable, str(fake)])
            self.assertFalse((output / "manifest.json").exists())
