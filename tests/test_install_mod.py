from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import install_mod, redbundle
from scripts.install_mod import (
    SWF_MEMBER,
    ModInstallError,
    assemble_package,
    install_package,
    preflight,
    uninstall_package,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = {
    "content/content0/scripts/game/gui/menus/gwintGameMenu.ws": b"class CR4GwintGameMenu { event  OnConfigUI() {} }\r\n",
    "content/content0/scripts/game/gui/main_menu/ingameMenu.ws": b"class CR4IngameMenu { event  OnConfigUI() {} }\r\n",
    "content/content0/scripts/game/inGameConfigWrapper.ws": b"import class CInGameConfigWrapper {}\r\n",
    "content/content0/scripts/game/facts.ws": b"import function FactsQueryLatestValue( ID : string ) : int;\r\n",
}
CARDS = "gameplay\\items\\def_gwint_cards_final.xml"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_game(root: Path, *, filelists: bool = False) -> tuple[Path, Path]:
    """A fake game folder and the pin file that matches it."""
    game = root / "game"
    screen = b"vanilla gwint screen \x00\x01"
    r4gui, _ = redbundle.pack(
        [
            (SWF_MEMBER, screen),
            ("gameplay\\gui_new\\swf\\gwint\\deck_builder.redswf", b"deck"),
        ]
    )
    xml, _ = redbundle.pack([(CARDS, b"<cards/>")])
    bundles = game / "content/content0/bundles"
    bundles.mkdir(parents=True)
    (bundles / "r4gui.bundle").write_bytes(r4gui)
    (bundles / "xml.bundle").write_bytes(xml)
    for relative, data in SCRIPTS.items():
        (game / relative).parent.mkdir(parents=True, exist_ok=True)
        (game / relative).write_bytes(data)
    menu = game / install_mod.MENU_DIR
    menu.mkdir(parents=True)
    (menu / "gameplay.xml").write_bytes(b"<UserConfig/>\r\n")
    if filelists:
        (menu / "dx11filelist.txt").write_bytes(b"gameplay.xml;\r\n")
        (menu / "dx12filelist.txt").write_bytes(b"gameplay.xml;")
    exe = game / "bin/x64_dx12/witcher3.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ\0 5.0.1048522  P4CL: 13413172  Stream \0")
    pins = json.loads(install_mod.GAME_PINS.read_text(encoding="utf-8"))
    pins["bundle_members"] = [
        {
            "bundle": "content/content0/bundles/r4gui.bundle",
            "member": SWF_MEMBER,
            "size": len(screen),
            "sha256": sha(screen),
            "role": "replaced",
            "why": "screen",
        },
        {
            "bundle": "content/content0/bundles/xml.bundle",
            "member": CARDS,
            "size": 8,
            "sha256": sha(b"<cards/>"),
            "role": "encoded",
            "why": "cards",
        },
    ]
    pins["files"] = [
        {
            "path": relative,
            "size": len(data),
            "sha256": sha(data),
            "role": "wrapped",
            "why": "wrapped here.",
        }
        for relative, data in SCRIPTS.items()
    ]
    pins["bundles_seen"] = {
        "r4gui.bundle": {"size": len(r4gui), "sha256": sha(r4gui)},
        "xml.bundle": {"size": len(xml), "sha256": sha(xml)},
    }
    pins_path = root / "game-pins.json"
    pins_path.write_text(json.dumps(pins), encoding="utf-8")
    return game, pins_path


def make_build(root: Path, pins_path: Path, variant: str = "ai") -> Path:
    build = root / f"build-{variant}"
    build.mkdir()
    redswf = b"patched gwint screen " + variant.encode()
    (build / "gwint_game.redswf").write_bytes(redswf)
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    spec = install_mod.VARIANTS[variant]
    sources = {
        Path(source).name: sha((ROOT / source).read_bytes())
        for source in (*spec.scripts, spec.menu)
    }
    (build / "manifest.json").write_text(
        json.dumps(
            {
                "status": "offline_compiled_and_export_verified",
                "variant": variant,
                "inputs_sha256": {
                    "gwint_game.redswf": pins["bundle_members"][0]["sha256"],
                    **sources,
                },
                "outputs_sha256": {"gwint_game.redswf": sha(redswf)},
            }
        ),
        encoding="utf-8",
    )
    return build


def snapshot(game: Path) -> tuple[dict[str, bytes], set[str]]:
    paths = list(game.rglob("*"))
    return (
        {
            path.relative_to(game).as_posix(): path.read_bytes()
            for path in paths
            if path.is_file()
        },
        {path.relative_to(game).as_posix() for path in paths if path.is_dir()},
    )


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="fake-witcher3-")
        self.root = Path(self._temp.name)
        self.runtime = self.root / "runtime"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def package(self, pins_path: Path, variant: str = "ai") -> Path:
        return assemble_package(
            variant,
            build_dir=make_build(self.root, pins_path, variant),
            package_dir=self.root / f"package-{variant}",
            pins_path=pins_path,
        )


class PreflightTests(Fixture):
    def test_each_changed_pinned_file_is_named_and_install_leaves_the_tree_untouched(
        self,
    ) -> None:
        game, pins_path = make_game(self.root)
        package = self.package(pins_path)
        pins = json.loads(pins_path.read_text(encoding="utf-8"))
        self.assertTrue(preflight(game, pins, "modW3GwentAI").ok)
        pristine = self.root / "pristine"
        shutil.copytree(game, pristine)
        changes = {
            "content/content0/scripts/game/gui/main_menu/ingameMenu.ws": "different patch",
            "content/content0/bundles/r4gui.bundle": "Gwent screen is not the one",
            "content/content0/bundles/xml.bundle": "card data differs",
        }
        for relative, meaning in changes.items():
            with self.subTest(relative=relative):
                if relative.endswith(".bundle"):
                    member = SWF_MEMBER if "r4gui" in relative else CARDS
                    data, _ = redbundle.pack([(member, b"other patch")])
                    (game / relative).write_bytes(data)
                else:
                    (game / relative).write_bytes(b"edited by another mod\r\n")
                check = preflight(game, pins, "modW3GwentAI")
                self.assertFalse(check.ok)
                self.assertEqual(len(check.problems), 1)
                self.assertIn(relative, check.problems[0])
                self.assertIn(meaning, check.problems[0])
                before = snapshot(game)
                with self.assertRaisesRegex(ModInstallError, re.escape(relative)):
                    install_package(game, package_dir=package, runtime_dir=self.runtime)
                self.assertEqual(snapshot(game), before)
                self.assertFalse((self.runtime / install_mod.RECEIPT_NAME).exists())
                shutil.copyfile(pristine / relative, game / relative)

    def test_another_mod_that_owns_the_gwent_screen_is_refused(self) -> None:
        game, pins_path = make_game(self.root)
        package = self.package(pins_path)
        other = game / "Mods/modBetterGwent/content"
        other.mkdir(parents=True)
        bundle, _ = redbundle.pack([(SWF_MEMBER, b"their screen")])
        (other / "blob0.bundle").write_bytes(bundle)
        with self.assertRaisesRegex(
            ModInstallError,
            "modBetterGwent also replaces gwint_game.redswf.*sorts before",
        ):
            install_package(game, package_dir=package, runtime_dir=self.runtime)

    def test_a_script_override_in_another_mod_is_a_note_not_a_refusal(self) -> None:
        game, pins_path = make_game(self.root)
        override = (
            game
            / "mods/mod0000_MergedFiles/content/scripts/game/gui/menus/gwintGameMenu.ws"
        )
        override.parent.mkdir(parents=True)
        override.write_bytes(b"merged\r\n")
        check = preflight(
            game, json.loads(pins_path.read_text(encoding="utf-8")), "modW3GwentAI"
        )
        self.assertTrue(check.ok)
        self.assertTrue(
            any("mod0000_MergedFiles overrides" in note for note in check.notes)
        )

    def test_folder_casing_reuses_lowercase_mods_and_refuses_both(self) -> None:
        game, pins_path = make_game(self.root)
        package = self.package(pins_path)
        (game / "mods/modSomethingElse/content").mkdir(parents=True)
        install_package(game, package_dir=package, runtime_dir=self.runtime)
        self.assertTrue((game / "mods/modW3GwentAI/content/blob0.bundle").is_file())
        self.assertFalse((game / "Mods").exists())
        uninstall_package(game, runtime_dir=self.runtime)
        (game / "Mods").mkdir()
        with self.assertRaisesRegex(ModInstallError, "both Mods and mods exist"):
            install_package(game, package_dir=package, runtime_dir=self.runtime)

    def test_a_copy_of_the_mod_installed_by_hand_is_refused(self) -> None:
        game, pins_path = make_game(self.root)
        package = self.package(pins_path)
        (game / "Mods/modW3GwentAIProbe/content").mkdir(parents=True)
        with self.assertRaisesRegex(
            ModInstallError, "modW3GwentAIProbe is already present"
        ):
            install_package(game, package_dir=package, runtime_dir=self.runtime)


class InstallTests(Fixture):
    def test_install_and_remove_archive_generated_scripts_and_preserve_shipped_scripts(
        self,
    ) -> None:
        game, pins_path = make_game(self.root)
        package = self.package(pins_path)
        cache = game / "content/content0/x64.final.redscripts"
        cooked = game / "content/content0/cookedfinal.redscripts"
        cache.write_bytes(b"old compiled mod")
        cooked.write_bytes(b"shipped scripts")
        receipt_path = install_package(
            game, package_dir=package, runtime_dir=self.runtime
        )
        receipt = json.loads(receipt_path.read_text())
        self.assertFalse(cache.exists())
        backup = self.runtime / receipt["backup_dir"]
        self.assertEqual(
            (backup / "script-cache-before-install/x64.final.redscripts").read_bytes(),
            b"old compiled mod",
        )
        cache.write_bytes(b"new compiled mod")
        uninstall_package(game, runtime_dir=self.runtime)
        self.assertFalse(cache.exists())
        self.assertEqual(
            (
                backup / "script-cache-before-uninstall/x64.final.redscripts"
            ).read_bytes(),
            b"new compiled mod",
        )
        self.assertEqual(cooked.read_bytes(), b"shipped scripts")

    def test_failed_install_restores_the_previous_compiled_cache(self) -> None:
        game, pins_path = make_game(self.root)
        package = self.package(pins_path)
        cache = game / "content/content0/x64.final.redscripts"
        cache.write_bytes(b"old compiled mod")
        before = snapshot(game)
        write_json = install_mod._write_json

        def fail_receipt(path, data):
            if path.name == install_mod.RECEIPT_NAME:
                raise OSError("receipt write failed")
            return write_json(path, data)

        with patch.object(install_mod, "_write_json", side_effect=fail_receipt):
            with self.assertRaisesRegex(OSError, "receipt write failed"):
                install_package(game, package_dir=package, runtime_dir=self.runtime)
        self.assertEqual(snapshot(game), before)

    def test_install_then_uninstall_restores_the_tree_byte_for_byte_and_the_receipt_names_every_file(
        self,
    ) -> None:
        game, pins_path = make_game(self.root, filelists=True)
        package = self.package(pins_path)
        menu = game / install_mod.MENU_DIR / "modW3GwentAI.xml"
        menu.write_bytes(
            b"older menu\x00"
        )  # an older hand install: backed up, then restored
        before = snapshot(game)

        receipt_path = install_package(
            game, package_dir=package, runtime_dir=self.runtime
        )

        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest = json.loads(
            (package / "package-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {entry["path"] for entry in receipt["files"]}, set(manifest["files"])
        )
        self.assertEqual(
            set(manifest["files"]),
            {
                "Mods/modW3GwentAI/content/blob0.bundle",
                "Mods/modW3GwentAI/content/metadata.store",
                "Mods/modW3GwentAI/content/scripts/local/gwai_settings.ws",
                "Mods/modW3GwentAI/content/scripts/local/gwai_playtrace.ws",
                "bin/config/r4game/user_config_matrix/pc/modW3GwentAI.xml",
            },
        )
        for entry in receipt["files"]:
            target = game / entry["path"]
            self.assertEqual(sha(target.read_bytes()), entry["installed_sha256"])
            self.assertEqual(
                entry["installed_sha256"], manifest["files"][entry["path"]]["sha256"]
            )
        menu_entry = next(
            entry
            for entry in receipt["files"]
            if entry["path"].endswith("modW3GwentAI.xml")
        )
        self.assertEqual(menu_entry["original_sha256"], sha(b"older menu\x00"))
        self.assertEqual(
            [edit["path"] for edit in receipt["filelist_edits"]],
            [
                f"{install_mod.MENU_DIR}/dx11filelist.txt",
                f"{install_mod.MENU_DIR}/dx12filelist.txt",
            ],
        )
        self.assertEqual(
            (game / install_mod.MENU_DIR / "dx11filelist.txt").read_bytes(),
            b"gameplay.xml;\r\nmodW3GwentAI.xml;\r\n",
        )
        self.assertEqual(
            (game / install_mod.MENU_DIR / "dx12filelist.txt").read_bytes(),
            b"gameplay.xml;\nmodW3GwentAI.xml;\n",
        )
        self.assertEqual(receipt["game_version"], "5.0.1048522 (P4CL 13413172)")
        self.assertEqual(len(receipt["game_files_checked"]), 6)

        uninstall_package(game, runtime_dir=self.runtime)

        self.assertEqual(snapshot(game), before)
        self.assertFalse(receipt_path.exists())

    def test_every_edition_installs_its_own_folder_and_restores(self) -> None:
        game, pins_path = make_game(self.root)
        before = snapshot(game)
        expected = {
            "probe": (
                "modW3GwentAIProbe",
                {"gwai_settings.ws", "gwai_timing_probe.ws", "gwai_playtrace.ws"},
                "modW3GwentAIProbe.xml",
            ),
            "recorder": (
                "modW3GwentRecorder",
                {"gwai_recorder.ws"},
                "modW3GwentRecorder.xml",
            ),
        }
        for variant, (name, scripts, menu) in expected.items():
            with self.subTest(variant=variant):
                install_package(
                    game,
                    package_dir=self.package(pins_path, variant),
                    runtime_dir=self.runtime,
                )
                self.assertEqual(
                    {
                        path.name
                        for path in (
                            game / f"Mods/{name}/content/scripts/local"
                        ).iterdir()
                    },
                    scripts,
                )
                self.assertTrue((game / install_mod.MENU_DIR / menu).is_file())
                uninstall_package(game, runtime_dir=self.runtime)
                self.assertEqual(snapshot(game), before)

    def test_uninstall_refuses_a_changed_installed_file_or_a_different_game(
        self,
    ) -> None:
        game, pins_path = make_game(self.root)
        install_package(
            game, package_dir=self.package(pins_path), runtime_dir=self.runtime
        )
        other = self.root / "other-game"
        other.mkdir()
        with self.assertRaisesRegex(ModInstallError, "does not match the receipt"):
            uninstall_package(other, runtime_dir=self.runtime)
        script = game / "Mods/modW3GwentAI/content/scripts/local/gwai_settings.ws"
        script.write_bytes(b"user edit")
        with self.assertRaisesRegex(ModInstallError, "target changed"):
            uninstall_package(game, runtime_dir=self.runtime)
        self.assertEqual(script.read_bytes(), b"user edit")
        self.assertTrue((self.runtime / install_mod.RECEIPT_NAME).is_file())

    def test_a_damaged_package_file_is_refused_before_anything_is_written(self) -> None:
        game, pins_path = make_game(self.root)
        package = self.package(pins_path)
        (package / "Mods/modW3GwentAI/content/metadata.store").write_bytes(b"truncated")
        before = snapshot(game)
        with self.assertRaisesRegex(
            ModInstallError, "missing or damaged.*metadata.store"
        ):
            install_package(game, package_dir=package, runtime_dir=self.runtime)
        self.assertEqual(snapshot(game), before)

    def test_a_build_from_a_different_vanilla_screen_is_not_packaged(self) -> None:
        _, pins_path = make_game(self.root)
        build = make_build(self.root, pins_path)
        manifest = json.loads((build / "manifest.json").read_text(encoding="utf-8"))
        manifest["inputs_sha256"]["gwint_game.redswf"] = "0" * 64
        (build / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            ModInstallError, "different vanilla gwint_game.redswf"
        ):
            assemble_package(
                "ai",
                build_dir=build,
                package_dir=self.root / "package",
                pins_path=pins_path,
            )

    def test_a_package_never_pairs_the_screen_with_scripts_the_build_did_not_use(
        self,
    ) -> None:
        _, pins_path = make_game(self.root)
        build = make_build(self.root, pins_path)
        manifest = json.loads((build / "manifest.json").read_text(encoding="utf-8"))
        manifest["inputs_sha256"]["gwai_settings.ws"] = "1" * 64
        (build / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(
            ModInstallError,
            "the build used gwai_settings.ws 1111.*rebuild the ai edition",
        ):
            assemble_package(
                "ai",
                build_dir=build,
                package_dir=self.root / "package",
                pins_path=pins_path,
            )

    def test_build_package_runs_the_edition_build_then_assembles_it(self) -> None:
        _, pins_path = make_game(self.root)
        variant = replace(
            install_mod.VARIANTS["probe"],
            build_dir=make_build(self.root, pins_path, "probe"),
            package_dir=self.root / "package-probe",
        )
        with (
            patch.dict(install_mod.VARIANTS, {"probe": variant}),
            patch("scripts.install_mod.GAME_PINS", pins_path),
            patch("scripts.install_mod.subprocess.run") as run,
        ):
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            package = install_mod.build_package(
                "probe", checkpoint=self.root / "checkpoint.pt"
            )
        self.assertIn("--probe", run.call_args.args[0])
        self.assertTrue(
            (package / "Mods/modW3GwentAIProbe/content/blob0.bundle").is_file()
        )
        self.assertTrue(
            (
                package
                / "Mods/modW3GwentAIProbe/content/scripts/local/gwai_timing_probe.ws"
            ).is_file()
        )


class BuildDefaultsTests(unittest.TestCase):
    def test_build_cli_uses_the_documented_model_location(self) -> None:
        with patch("scripts.install_mod.build_package") as build:
            self.assertEqual(install_mod.main(["--build"]), 0)
        self.assertEqual(
            build.call_args.kwargs["checkpoint"], ROOT / ".runtime/models/hard.pt"
        )

    def test_package_build_uses_the_documented_model_location(self) -> None:
        with (
            patch("scripts.install_mod.subprocess.run") as run,
            patch("scripts.install_mod.assemble_package"),
        ):
            run.return_value = SimpleNamespace(returncode=0)
            install_mod.build_package()
        command = run.call_args.args[0]
        self.assertEqual(
            command[command.index("--checkpoint") + 1],
            str(ROOT / ".runtime/models/hard.pt"),
        )


class DiscoveryTests(unittest.TestCase):
    def test_steam_libraries_and_the_proton_documents_folder_are_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            library = home / "games/SteamLibrary"
            game = library / "steamapps/common/The Witcher 3"
            (game / "content/content0").mkdir(parents=True)
            vdf = home / ".local/share/Steam/steamapps/libraryfolders.vdf"
            vdf.parent.mkdir(parents=True)
            vdf.write_text(
                f'"libraryfolders"\n{{\n "1"\n {{\n  "path"\t\t"{library}"\n }}\n}}\n',
                encoding="utf-8",
            )
            (home / ".steam").mkdir()
            (home / ".steam/steam").symlink_to(home / ".local/share/Steam")
            self.assertEqual(install_mod.find_game_dirs(home), [game.resolve()])
            self.assertEqual(
                install_mod.proton_documents(game),
                library.resolve()
                / "steamapps/compatdata/292030/pfx/drive_c/users/steamuser/Documents/The Witcher 3",
            )

    def test_report_prints_the_mod_sections_of_the_settings_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            documents = Path(temp)
            (documents / "dx12user.settings").write_text(
                '[Gameplay]\nX=1\n\n[W3GwentTimingProbe]\ncount=2\nrecord_000000={"ms":4}\n\n[W3GwentAI]\nGwaiEnabled=true\n',
                encoding="utf-8",
            )
            report = install_mod.first_match_report(
                Path("/game"), documents, runtime_dir=documents / "none"
            )
        self.assertIn('[W3GwentTimingProbe]\ncount=2\nrecord_000000={"ms":4}', report)
        self.assertIn("[W3GwentAI]\nGwaiEnabled=true", report)
        self.assertNotIn("[Gameplay]", report)
        self.assertIn("user.settings: not present", report)


class FirstMatchSignalTests(unittest.TestCase):
    def test_the_three_in_game_banners_read_differently(self) -> None:
        source = (ROOT / "scripts/build_mod.py").read_text(encoding="utf-8")
        self.assertIn('" greedy-fallback network "', source)
        self.assertIn('" network "', source)
        self.assertIn(
            'PushMessage("W3 Gwent AI: fallback (vanilla AI plays this turn)")', source
        )
        self.assertIn(
            'PushMessage("W3 Gwent AI: " + difficulty + " " + detail)', source
        )
        for report in re.findall(r'gwaiReportDecision\(profile,false,"(\w+)', source):
            self.assertIn(report, {"pass", "leader", "play"})
        self.assertEqual(
            source.count(
                '(greedyFallback ? " greedy-fallback network " : " network ")'
            ),
            3,
        )


if __name__ == "__main__":
    unittest.main()
