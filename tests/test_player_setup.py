import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_player

from tests import test_install_mod as install_fixture

ROOT = Path(__file__).resolve().parents[1]


class ToolArchiveTests(unittest.TestCase):
    def test_prepare_preserves_unicode_across_cp1252_parent_and_child_pipes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Joueur été"
            scripts = root / "scripts"
            scripts.mkdir(parents=True)
            (scripts / "extract_game.py").write_text(
                "import sys\n"
                "sys.stdout.reconfigure(newline='\\r\\n')\n"
                "if not sys.flags.utf8_mode: sys.stdout.reconfigure(encoding='cp1252')\n"
                "print('Installed resources: été')\n",
                encoding="utf-8",
            )
            (scripts / "decompile_game.py").write_text(
                "import sys\nprint('Compiler path: été / 日本語')\nsys.exit(7)\n",
                encoding="utf-8",
            )
            stdout_bytes, stderr_bytes = io.BytesIO(), io.BytesIO()
            stdout = io.TextIOWrapper(stdout_bytes, encoding="cp1252")
            stderr = io.TextIOWrapper(stderr_bytes, encoding="cp1252")
            with (
                patch.object(setup_player, "verify_distribution"),
                patch.object(setup_player, "check_game"),
                patch.object(
                    setup_player, "ensure_tools", return_value=dict(os.environ)
                ),
                patch("sys.stdout", stdout),
                patch("sys.stderr", stderr),
            ):
                result = setup_player.main(
                    ["--prepare-only", "--game-dir", str(root / "game")], root=root
                )
            stdout.flush()
            stderr.flush()
            self.assertEqual(result, 1)
            self.assertIn(
                "Installed resources: été", stdout_bytes.getvalue().decode("utf-8")
            )
            self.assertNotIn(b"\r", stdout_bytes.getvalue())
            self.assertIn(
                "Compiler path: été / 日本語", stdout_bytes.getvalue().decode("utf-8")
            )
            self.assertIn("exit 7", stderr_bytes.getvalue().decode("utf-8"))
            log = (root / ".runtime/setup-player.log").read_text(encoding="utf-8")
            self.assertIn("Installed resources: été", log)
            self.assertIn("日本語", log)
            self.assertNotIn("\ufffd", log)
            self.assertIn(
                b"\xc3\xa9t\xc3\xa9\r\n",
                (root / ".runtime/setup-player.log").read_bytes(),
            )

    def cached_archive(self, root):
        source = root / "original.zip"
        with zipfile.ZipFile(source, "w") as output:
            output.writestr("ffdec-cli.jar", b"loader")
            output.writestr("lib/compiler.jar", b"compiler")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        spec = setup_player.ToolArchive(
            "ffdec",
            "1",
            "https://example.invalid/tool.zip",
            digest,
            "ffdec-cli.jar",
            "zip",
            "",
        )
        archive = root / "tools/archives" / f"{digest}.zip"
        archive.parent.mkdir(parents=True)
        shutil.copyfile(source, archive)
        return spec, archive

    def test_verified_archive_alone_can_restore_an_absent_tool_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, archive = self.cached_archive(root)
            with patch.object(setup_player, "download_file") as download:
                executable = setup_player.ensure_tool(
                    spec,
                    root / "tools",
                    offline=True,
                    download=download,
                )
            download.assert_not_called()
            self.assertEqual(executable.read_bytes(), b"loader")
            self.assertEqual(
                (executable.parent / "lib/compiler.jar").read_bytes(), b"compiler"
            )
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(), spec.sha256
            )
            self.assertEqual(
                setup_player.ensure_tool(spec, root / "tools", offline=True), executable
            )

    def test_corrupt_original_archive_is_preserved_and_never_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, archive = self.cached_archive(root)
            archive.write_bytes(b"damaged original archive")
            with self.assertRaisesRegex(
                setup_player.SetupError, "SHA-256|archive changed"
            ):
                setup_player.ensure_tool(spec, root / "tools", offline=True)
            self.assertFalse((root / "tools/ffdec").exists())
            self.assertEqual(archive.read_bytes(), b"damaged original archive")

    def test_original_archive_symlink_is_never_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec, archive = self.cached_archive(root)
            archive.unlink()
            try:
                archive.symlink_to(root / "original.zip")
            except OSError as exc:
                self.skipTest(f"This filesystem cannot create the test symlink: {exc}")
            with self.assertRaisesRegex(setup_player.SetupError, "symlink"):
                setup_player.ensure_tool(spec, root / "tools", offline=True)
            self.assertFalse((root / "tools/ffdec").exists())
            self.assertTrue(archive.is_symlink())

    def test_zip_paths_cannot_escape_or_install_a_symlink(self):
        for name in ("../escape", "C:/escape", "safe/../../escape", "safe\\..\\escape"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "tool.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(name, b"bad")
                with self.assertRaises(setup_player.SetupError):
                    setup_player.extract_tool_archive(archive, root / "out")
                self.assertFalse((root / "escape").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "tool.zip"
            with zipfile.ZipFile(archive, "w") as output:
                link = zipfile.ZipInfo("tool/link")
                link.create_system = 3
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                output.writestr(link, "../../outside")
            with self.assertRaisesRegex(setup_player.SetupError, "symlink"):
                setup_player.extract_tool_archive(archive, root / "out")

    def test_tar_cannot_escape_through_a_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "tool.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                link = tarfile.TarInfo("tool/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../outside"
                output.addfile(link)
                payload = tarfile.TarInfo("tool/link/payload")
                payload.size = 3
                output.addfile(payload, io.BytesIO(b"bad"))
            with self.assertRaises(setup_player.SetupError):
                setup_player.extract_tool_archive(archive, root / "out")
            self.assertFalse((root / "outside").exists())

    def test_cache_checks_companion_files_and_does_not_replace_damage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("ffdec-cli.jar", b"loader")
                output.writestr("lib/compiler.jar", b"compiler")
            spec = setup_player.ToolArchive(
                "ffdec",
                "1",
                "https://example.invalid/tool.zip",
                hashlib.sha256(archive.read_bytes()).hexdigest(),
                "ffdec-cli.jar",
                "zip",
                "",
            )
            downloads = []

            def download(url, target):
                downloads.append(url)
                target.write_bytes(archive.read_bytes())

            first = setup_player.ensure_tool(spec, root / "tools", download=download)
            self.assertEqual(first, root / "tools/ffdec/ffdec-cli.jar")
            self.assertEqual(
                setup_player.ensure_tool(spec, root / "tools", download=download), first
            )
            self.assertEqual(len(downloads), 1)
            companion = root / "tools/ffdec/lib/compiler.jar"
            companion.write_bytes(b"changed")
            receipt_path = root / "tools/ffdec/_tool-receipt.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["files"]["lib/compiler.jar"] = {
                "sha256": hashlib.sha256(b"changed").hexdigest(),
                "bytes": 7,
            }
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(setup_player.SetupError, "cache.*changed"):
                setup_player.ensure_tool(spec, root / "tools", download=download)
            self.assertEqual(companion.read_bytes(), b"changed")
            self.assertEqual(len(downloads), 1)

    def test_wrong_download_hash_never_installs_a_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = setup_player.ToolArchive(
                "ffdec",
                "1",
                "https://example.invalid/tool.zip",
                "0" * 64,
                "ffdec-cli.jar",
                "zip",
                "",
            )
            with self.assertRaisesRegex(setup_player.SetupError, "SHA-256"):
                setup_player.ensure_tool(
                    spec,
                    root / "tools",
                    download=lambda url, path: path.write_bytes(b"wrong"),
                )
            self.assertFalse((root / "tools/ffdec").exists())


class JavaCapabilityTests(unittest.TestCase):
    def test_non_utf8_version_output_rejects_system_runtime(self):
        with (
            patch.object(
                setup_player.shutil, "which", return_value="/runtime/bin/java"
            ),
            patch.object(
                setup_player.subprocess,
                "run",
                side_effect=UnicodeDecodeError(
                    "utf-8", b"vendor \xe9", 7, 8, "invalid byte"
                ),
            ),
            patch.object(setup_player, "_source_compiler_available") as source_probe,
        ):
            self.assertIsNone(setup_player._system_tool("java", 11))
        source_probe.assert_not_called()

    def test_source_probe_requires_unicode_utf8_on_both_streams(self):
        marker = setup_player.JAVA_SOURCE_MARKER.encode("utf-8")
        for stdout, stderr in ((b"legacy \xe9", marker), (marker, b"legacy \xe9")):
            with (
                self.subTest(stdout=stdout, stderr=stderr),
                patch.object(
                    setup_player.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout, stderr),
                ),
            ):
                self.assertFalse(setup_player._source_compiler_available(Path("java")))

    def test_source_compiler_without_a_javac_file_is_usable(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "runtime/bin/java"
            source_paths = []

            def run(command, **kwargs):
                if "-version" in command:
                    return subprocess.CompletedProcess(
                        command, 0, "", 'openjdk version "21.0.13"'
                    )
                source = Path(command[-1])
                self.assertTrue(source.is_file())
                source_paths.append(source)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    setup_player.JAVA_SOURCE_MARKER.encode("utf-8"),
                    setup_player.JAVA_SOURCE_MARKER.encode("utf-8"),
                )

            with (
                patch.object(
                    setup_player.shutil, "which", return_value=str(executable)
                ),
                patch.object(setup_player.subprocess, "run", side_effect=run),
            ):
                self.assertEqual(
                    setup_player._system_tool("java", 11), executable.resolve()
                )
            self.assertEqual(len(source_paths), 1)
            self.assertFalse(source_paths[0].exists())

    def test_present_compiler_filename_cannot_hide_a_failed_source_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "runtime/bin/java"
            executable.parent.mkdir(parents=True)
            executable.with_name(
                "javac.exe" if setup_player.os.name == "nt" else "javac"
            ).write_bytes(b"stale launcher")
            results = [
                subprocess.CompletedProcess([], 0, "", 'openjdk version "21.0.13"'),
                subprocess.CompletedProcess(
                    [], 1, "", "source compiler is unavailable"
                ),
            ]
            with (
                patch.object(
                    setup_player.shutil, "which", return_value=str(executable)
                ),
                patch.object(setup_player.subprocess, "run", side_effect=results),
            ):
                self.assertIsNone(setup_player._system_tool("java", 11))

    def test_source_probe_timeout_is_unsuitable_and_its_source_is_removed(self):
        source_paths = []

        def run(command, **kwargs):
            if "-version" in command:
                return subprocess.CompletedProcess(
                    command, 0, "", 'openjdk version "21.0.13"'
                )
            source_paths.append(Path(command[-1]))
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with (
            patch.object(
                setup_player.shutil, "which", return_value="/runtime/bin/java"
            ),
            patch.object(setup_player.subprocess, "run", side_effect=run),
        ):
            self.assertIsNone(setup_player._system_tool("java", 11))
        self.assertEqual(len(source_paths), 1)
        self.assertFalse(source_paths[0].exists())


class SetupFlowTests(unittest.TestCase):
    def test_fresh_direct_help_does_not_write_import_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "scripts"
            scripts.mkdir()
            for name in ("setup_player.py", "cli_encoding.py"):
                shutil.copyfile(ROOT / "scripts" / name, scripts / name)
            before = {path.relative_to(root) for path in root.rglob("*")}
            result = subprocess.run(
                [sys.executable, "-E", str(scripts / "setup_player.py"), "--help"],
                cwd=root,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                {path.relative_to(root) for path in root.rglob("*")}, before
            )

    def managed_receipt(self, root, game):
        names = [
            "Mods/modW3GwentAI/content/blob0.bundle",
            "Mods/modW3GwentAI/content/metadata.store",
            "Mods/modW3GwentAI/content/scripts/local/gwai_settings.ws",
            "Mods/modW3GwentAI/content/scripts/local/gwai_playtrace.ws",
            "bin/config/r4game/user_config_matrix/pc/modW3GwentAI.xml",
        ]
        files = []
        for name in names:
            path = game / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            files.append(
                {
                    "path": name,
                    "installed_sha256": hashlib.sha256(name.encode()).hexdigest(),
                }
            )
        receipt = {
            "format": "witcher3-gwent-ai-install-receipt-v1",
            "game_dir": str(game),
            "mod_name": "modW3GwentAI",
            "files": files,
        }
        path = root / ".runtime/mod-install-receipt.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(receipt))
        return path, receipt

    def test_managed_receipt_requires_menu_and_five_unique_normalized_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            path, receipt = self.managed_receipt(root, game)
            self.assertEqual(setup_player._managed_install(root, game), "modW3GwentAI")
            self.assertEqual(
                setup_player._managed_install(root, game / ".." / game.name),
                "modW3GwentAI",
            )
            receipt["files"][-1] = dict(receipt["files"][0])
            receipt["files"][-1]["path"] = receipt["files"][-1]["path"].replace(
                "/", "\\"
            )
            path.write_text(json.dumps(receipt))
            with (
                patch.object(setup_player._installer(), "load_pins", return_value={}),
                patch.object(setup_player._installer(), "preflight") as preflight,
            ):
                with self.assertRaisesRegex(
                    setup_player.SetupError, "duplicate|expected files"
                ):
                    setup_player.check_game(root, game)
            preflight.assert_not_called()

    def test_new_download_checks_verified_previous_install_without_changing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, pins_path = install_fixture.make_game(root)
            old, new = root / "old download", root / "new download"
            receipt, _ = self.managed_receipt(old, game)
            (new / "mod").mkdir(parents=True)
            shutil.copy2(pins_path, new / "mod/game-pins.json")
            before_game = install_fixture.snapshot(game)
            before_receipt = receipt.read_bytes()
            setup_player.check_game(new, game, installed_from=old)
            self.assertEqual(install_fixture.snapshot(game), before_game)
            self.assertEqual(receipt.read_bytes(), before_receipt)
            self.assertFalse((new / ".runtime").exists())
            with self.assertRaisesRegex(setup_player.SetupError, "compatibility"):
                setup_player.check_game(new, game)

    def test_previous_install_must_have_a_valid_receipt_for_this_game(self):
        for failure in ("missing", "changed", "other-game"):
            with (
                self.subTest(failure=failure),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                game = root / "game"
                old = root / "old"
                receipt_path, receipt = self.managed_receipt(old, game)
                if failure == "missing":
                    receipt_path.unlink()
                elif failure == "changed":
                    (game / receipt["files"][0]["path"]).write_bytes(b"changed")
                else:
                    receipt["game_dir"] = str(root / "another game")
                    receipt_path.write_text(json.dumps(receipt))
                with patch.object(setup_player._installer(), "preflight") as preflight:
                    with self.assertRaises(setup_player.SetupError):
                        setup_player.check_game(root / "new", game, installed_from=old)
                preflight.assert_not_called()

    def test_previous_install_option_is_limited_to_read_only_modes(self):
        for mode in ([], ["--uninstall"], ["--report"]):
            with self.subTest(mode=mode), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    setup_player.main([*mode, "--installed-from", "old-download"])
                self.assertEqual(error.exception.code, 2)

    def test_prepare_passes_the_named_old_download_to_compatibility_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game, old = root / "game", root / "old"
            with (
                patch.object(setup_player, "verify_distribution"),
                patch.object(setup_player, "check_game") as check,
                patch.object(setup_player, "ensure_tools", return_value={}),
                patch.object(setup_player, "prepare", return_value=root / "package"),
                patch.object(setup_player, "install") as install,
            ):
                self.assertEqual(
                    setup_player.main(
                        [
                            "--prepare-only",
                            "--installed-from",
                            str(old),
                            "--game-dir",
                            str(game),
                        ],
                        root=root,
                    ),
                    0,
                )
            check.assert_called_once_with(root, game.resolve(), installed_from=old)
            install.assert_not_called()

    def test_check_is_read_only_and_never_downloads_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            with (
                patch.object(setup_player, "verify_distribution") as verify,
                patch.object(setup_player, "check_game") as check,
                patch.object(setup_player, "ensure_tools") as tools,
                patch.object(setup_player, "prepare") as prepare,
            ):
                self.assertEqual(
                    setup_player.main(["--check", "--game-dir", str(game)], root=root),
                    0,
                )
            verify.assert_called_once_with(root)
            check.assert_called_once_with(root, game.resolve(), installed_from=None)
            tools.assert_not_called()
            prepare.assert_not_called()
            self.assertEqual(list(root.iterdir()), [game])

    def test_prepare_only_does_not_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            with (
                patch.object(setup_player, "verify_distribution"),
                patch.object(setup_player, "check_game"),
                patch.object(
                    setup_player, "ensure_tools", return_value={"PATH": "tools"}
                ),
                patch.object(
                    setup_player,
                    "prepare",
                    return_value=root / ".runtime/player-package",
                ) as prepare,
                patch.object(setup_player, "install") as install,
            ):
                self.assertEqual(
                    setup_player.main(
                        ["--prepare-only", "--game-dir", str(game)], root=root
                    ),
                    0,
                )
            prepare.assert_called_once()
            install.assert_not_called()

    def test_failed_preparation_never_installs_or_hides_the_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            game.mkdir()
            with (
                patch.object(setup_player, "verify_distribution"),
                patch.object(setup_player, "check_game"),
                patch.object(setup_player, "ensure_tools", return_value={}),
                patch.object(
                    setup_player,
                    "prepare",
                    side_effect=setup_player.SetupError("compiler failed"),
                ),
                patch.object(setup_player, "install") as install,
                patch("sys.stderr", new_callable=io.StringIO) as errors,
            ):
                self.assertEqual(
                    setup_player.main(["--game-dir", str(game)], root=root), 1
                )
            self.assertIn("compiler failed", errors.getvalue())
            install.assert_not_called()

    def test_pipeline_uses_distribution_root_and_stops_after_failed_step(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                if str(command[3]).endswith("decompile_game.py"):
                    raise setup_player.SetupError("decompiler failed")

            with patch.object(setup_player, "run_step", side_effect=run):
                with self.assertRaisesRegex(
                    setup_player.SetupError, "decompiler failed"
                ):
                    setup_player.prepare(root, root / "game", {"PATH": "tools"})
            self.assertEqual(len(calls), 2)
            for command, kwargs in calls:
                self.assertTrue(Path(command[3]).is_relative_to(root))
                self.assertEqual(kwargs["cwd"], root)
                self.assertEqual(kwargs["env"], {"PATH": "tools"})

    def test_release_inventory_rejects_modified_runtime_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            source = root / "scripts/install_mod.py"
            source.write_text("original")
            manifest = {
                "format": "witcher3-player-release-v1",
                "files": {
                    "scripts/install_mod.py": {
                        "sha256": hashlib.sha256(b"original").hexdigest(),
                        "bytes": 8,
                    }
                },
            }
            (root / "release-manifest.json").write_text(json.dumps(manifest))
            setup_player.verify_distribution(root)
            source.write_text("modified")
            with self.assertRaisesRegex(
                setup_player.SetupError, "changed.*install_mod"
            ):
                setup_player.verify_distribution(root)

    def test_unlisted_source_is_rejected_but_local_outputs_are_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts").mkdir()
            script = root / "scripts/setup_player.py"
            script.write_bytes(b"original")
            (root / "release-manifest.json").write_text(
                json.dumps(
                    {
                        "format": setup_player.RELEASE_FORMAT,
                        "files": {
                            "scripts/setup_player.py": {
                                "sha256": hashlib.sha256(b"original").hexdigest(),
                                "bytes": 8,
                            }
                        },
                    }
                )
            )
            (root / ".runtime").mkdir()
            (root / ".runtime/local.log").write_text("local output")
            setup_player.verify_distribution(root)
            (root / "scripts/json.py").write_text("unlisted import shadow")
            with self.assertRaisesRegex(setup_player.SetupError, "Unexpected file"):
                setup_player.verify_distribution(root)

    def test_failed_child_step_keeps_diagnostic_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                with self.assertRaisesRegex(setup_player.SetupError, "exit 7"):
                    setup_player.run_step(
                        [
                            sys.executable,
                            "-c",
                            "import sys;print('compiler detail');sys.exit(7)",
                        ],
                        label="Compile",
                        cwd=root,
                        env={},
                    )
            self.assertIn("compiler detail", output.getvalue())
            self.assertIn(
                "compiler detail", (root / ".runtime/setup-player.log").read_text()
            )

    def test_invalid_child_encoding_fails_with_original_bytes_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch("sys.stdout", new_callable=io.StringIO) as output,
                self.assertRaisesRegex(
                    setup_player.SetupError, "not UTF-8; original bytes"
                ) as failure,
            ):
                setup_player.run_step(
                    [
                        sys.executable,
                        "-c",
                        "import sys;sys.stdout.buffer.write(b'legacy \\xe9\\n');sys.exit(7)",
                    ],
                    label="Compile",
                    cwd=root,
                    env={},
                )
            self.assertEqual(
                (root / ".runtime/setup-player.log").read_bytes(),
                b"Compile\nlegacy \xe9\n",
            )
            self.assertNotIn("\ufffd", output.getvalue())
            self.assertIn("child exit 7", str(failure.exception))

    def test_direct_extractor_cli_reports_unicode_errors_on_a_cp1252_pipe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "Missing été 日本語"
            environment = {**os.environ, "PYTHONIOENCODING": "cp1252"}
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/extract_game.py"),
                    "--game-dir",
                    str(game),
                    "--output",
                    str(root / "output"),
                ],
                env=environment,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("Missing été 日本語", result.stderr.decode("utf-8"))
            self.assertNotIn(b"UnicodeEncodeError", result.stderr)


class ShellLauncherTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("sh"), "POSIX shell is required for the shell launcher"
    )
    def test_uninstall_does_not_require_executable_bits_after_zip_extraction(self):
        with tempfile.TemporaryDirectory(prefix="mod download ") as temporary:
            root = Path(temporary)
            install = root / "Install.sh"
            install.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            install.chmod(0o644)
            uninstall = root / "Uninstall.sh"
            uninstall.write_bytes((ROOT / "Uninstall.sh").read_bytes())
            result = subprocess.run(
                [shutil.which("sh"), uninstall.as_posix(), "--game-dir", "game folder"],
                cwd=root,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(), ["--uninstall", "--game-dir", "game folder"]
            )


if __name__ == "__main__":
    unittest.main()
