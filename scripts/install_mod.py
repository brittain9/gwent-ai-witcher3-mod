#!/usr/bin/env python3
"""Build, check, install, or remove the Witcher 3 Gwent AI mod.

  --package DIR             check the game, then install DIR (a finished build such as
                            .runtime/mod-build, or an assembled package)
  --check --package DIR     run only the check
  --uninstall               restore the game folder from the install receipt
  --report                  print what to send back after the first match
  --build                   run the offline build (FFDec, checkpoint export), then assemble

Without --game-dir the script looks for the Steam install on Linux (native and Flatpak
Steam, every library in libraryfolders.vdf). It never starts the game or reads saves.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import locale
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from scripts import cli_encoding, extract_game, redbundle
except ImportError:  # run as scripts/install_mod.py
    import cli_encoding
    import extract_game
    import redbundle


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKPOINT = ROOT / ".runtime/models/hard.pt"
RUNTIME_DIR = ROOT / ".runtime"
GAME_PINS = ROOT / "mod/game-pins.json"
RECEIPT_NAME = "mod-install-receipt.json"
RECEIPT_FORMAT = "witcher3-gwent-ai-install-receipt-v1"
PACKAGE_FORMAT = "witcher3-gwent-ai-package-v1"
BUILD_STATUS = "offline_compiled_and_export_verified"
MENU_DIR = PurePosixPath("bin/config/r4game/user_config_matrix/pc")
FILELIST_NAMES = ("dx11filelist.txt", "dx12filelist.txt")
SCRIPT_CACHE = PurePosixPath("content/content0/x64.final.redscripts")
MODS_DIR = "Mods"
SWF_MEMBER = "gameplay\\gui_new\\swf\\gwint\\gwint_game.redswf"
STEAM_APP_ID = 292030
GAME_FOLDER = "The Witcher 3"
EXE_VERSION_RE = re.compile(rb"(\d+\.\d+\.\d+)  P4CL: (\d+)")
GAME_PROCESS_NAMES = frozenset(
    {
        "witcher3.exe",
        "witcher3",
        "redlauncher.exe",
        "redlauncher",
        "redprelauncher.exe",
        "redprelauncher",
        "redprelauncher.",
    }
)


@dataclass(frozen=True)
class Variant:
    mod_name: str
    build_dir: Path
    package_dir: Path
    scripts: tuple[str, ...]
    menu: str


VARIANTS = {
    "ai": Variant(
        "modW3GwentAI",
        RUNTIME_DIR / "mod-build",
        RUNTIME_DIR / "mod-package",
        (
            "mod/ai/settings/scripts/local/gwai_settings.ws",
            "mod/trace/settings/scripts/local/gwai_playtrace.ws",
        ),
        "mod/ai/settings/menu/modW3GwentAI.xml",
    ),
    "probe": Variant(
        "modW3GwentAIProbe",
        RUNTIME_DIR / "mod-build-probe",
        RUNTIME_DIR / "mod-package-probe",
        (
            "mod/ai/settings/scripts/local/gwai_settings.ws",
            "mod/probe-timing/settings/scripts/local/gwai_timing_probe.ws",
            "mod/trace/settings/scripts/local/gwai_playtrace.ws",
        ),
        "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
    ),
    "recorder": Variant(
        "modW3GwentRecorder",
        RUNTIME_DIR / "mod-build-recorder",
        RUNTIME_DIR / "mod-package-recorder",
        ("mod/recorder/settings/scripts/local/gwai_recorder.ws",),
        "mod/recorder/settings/menu/modW3GwentRecorder.xml",
    ),
}
OUR_MOD_NAMES = {variant.mod_name.lower() for variant in VARIANTS.values()}


class ModInstallError(RuntimeError):
    """Raised when a package, the game folder or an install receipt is not as expected."""


def running_game_processes() -> list[str]:
    """Read executable names only; process arguments are never inspected."""
    windows = platform.system() == "Windows"
    command = (
        ["tasklist", "/FO", "CSV", "/NH"] if windows else ["ps", "-e", "-o", "comm="]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding=locale.getencoding(),
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ModInstallError(
            "Cannot check whether the game is running; no game files were changed"
        ) from exc
    if result.returncode or not result.stdout.strip():
        raise ModInstallError(
            "Cannot read the process list; no game files were changed"
        )
    if windows:
        rows = list(csv.reader(io.StringIO(result.stdout)))
        if any(len(row) < 2 or not row[1].strip().isdigit() for row in rows if row):
            raise ModInstallError(
                "Cannot parse the Windows process list; no game files were changed"
            )
        names = [row[0] for row in rows if row]
    else:
        names = result.stdout.splitlines()
    return sorted(
        {
            Path(name.strip()).name
            for name in names
            if Path(name.strip()).name.casefold() in GAME_PROCESS_NAMES
        }
    )


def require_game_stopped() -> None:
    if names := running_game_processes():
        raise ModInstallError(
            "Close The Witcher 3 and its REDlauncher before installing or removing the mod "
            f"(running: {', '.join(names)})"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise ModInstallError(f"unsafe package path: {value!r}")
    return path


def load_pins(path: Path = GAME_PINS) -> dict[str, Any]:
    pins = json.loads(path.read_text(encoding="utf-8"))
    if pins.get("format") != "witcher3-gwent-ai-game-pins-v1":
        raise ModInstallError(f"{path}: unknown game-pin format")
    return pins


def _replaced_pin(pins: dict[str, Any]) -> dict[str, Any]:
    matches = [
        pin
        for pin in pins["bundle_members"]
        if pin["role"] == "replaced" and pin["member"] == SWF_MEMBER
    ]
    if len(matches) != 1:
        raise ModInstallError(
            "game pins must name the replaced gwint_game.redswf exactly once"
        )
    return matches[0]


# --- package -----------------------------------------------------------------------------


def assemble_package(
    variant: str,
    *,
    build_dir: Path | None = None,
    package_dir: Path | None = None,
    pins_path: Path | None = None,
) -> Path:
    """Pack a finished build into the folder layout the game loads, and verify it."""
    if variant not in VARIANTS:
        raise ModInstallError(f"unknown mod variant: {variant}")
    spec = VARIANTS[variant]
    build_dir = (build_dir or spec.build_dir).resolve()
    package_dir = (package_dir or spec.package_dir).resolve()
    manifest_path = build_dir / "manifest.json"
    report = json.loads(manifest_path.read_text(encoding="utf-8"))
    if report.get("status") != BUILD_STATUS:
        raise ModInstallError(
            f"offline mod build has unexpected status: {report.get('status')}"
        )
    if report.get("variant") != variant:
        raise ModInstallError(
            f"offline mod build returned {report.get('variant')!r} for {variant!r}"
        )
    redswf = (build_dir / "gwint_game.redswf").read_bytes()
    if hashlib.sha256(redswf).hexdigest() != report.get("outputs_sha256", {}).get(
        "gwint_game.redswf"
    ):
        raise ModInstallError(
            "build gwint_game.redswf does not match its manifest's output hash"
        )
    pins_path = pins_path or GAME_PINS
    pins = load_pins(pins_path)
    vanilla_input = report.get("inputs_sha256", {}).get("gwint_game.redswf")
    if vanilla_input != _replaced_pin(pins)["sha256"]:
        raise ModInstallError(
            "the build was made from a different vanilla gwint_game.redswf "
            f"({vanilla_input}) than the pinned game ({_replaced_pin(pins)['sha256']})"
        )

    recorded = report.get("inputs_sha256", {})
    for source in (*spec.scripts, spec.menu):
        name = Path(source).name
        actual = _sha256(ROOT / source)
        if (name in recorded or name.endswith(".ws")) and recorded.get(name) != actual:
            raise ModInstallError(
                f"the build used {name} {str(recorded.get(name))[:16]}, but this checkout has {actual[:16]}; "
                f"rebuild the {variant} edition so the package's scripts match its Gwent screen"
            )

    bundle, store = redbundle.pack([(SWF_MEMBER, redswf)])
    if package_dir.exists():
        shutil.rmtree(package_dir)
    content = package_dir / MODS_DIR / spec.mod_name / "content"
    (content / "scripts/local").mkdir(parents=True)
    (content / redbundle.BUNDLE_NAME).write_bytes(bundle)
    (content / "metadata.store").write_bytes(store)
    _verify_bundle(content, redswf)
    for script in spec.scripts:
        shutil.copy2(ROOT / script, content / "scripts/local" / Path(script).name)
    menu_target = package_dir / MENU_DIR / Path(spec.menu).name
    menu_target.parent.mkdir(parents=True)
    shutil.copy2(ROOT / spec.menu, menu_target)
    shutil.copy2(manifest_path, package_dir / "build-manifest.json")
    shutil.copy2(pins_path, package_dir / "game-pins.json")
    files = {
        path.relative_to(package_dir).as_posix(): {
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(package_dir.rglob("*"))
        if path.is_file()
        and path.relative_to(package_dir).parts[0] in (MODS_DIR, "bin")
    }
    _write_json(
        package_dir / "package-manifest.json",
        {
            "format": PACKAGE_FORMAT,
            "variant": variant,
            "mod_name": spec.mod_name,
            "build_manifest_sha256": _sha256(manifest_path),
            "redswf_sha256": report["outputs_sha256"]["gwint_game.redswf"],
            "files": files,
        },
    )
    return package_dir


def _verify_bundle(content: Path, redswf: bytes) -> None:
    """The packed bundle must give back the build's exact bytes through the game's index."""
    bundle_path = content / redbundle.BUNDLE_NAME
    entries = extract_game.parse_bundle(bundle_path)
    if [entry.name for entry in entries] != [SWF_MEMBER]:
        raise ModInstallError("packed bundle does not hold exactly the Gwent screen")
    if extract_game.read_entry(bundle_path, entries[0]) != redswf:
        raise ModInstallError(
            "packed bundle does not return the build's gwint_game.redswf bytes"
        )
    store = redbundle.read_store((content / "metadata.store").read_bytes())
    item = store.files[0] if len(store.files) == 1 else None
    if (
        item is None
        or item.path != SWF_MEMBER
        or item.bundle != redbundle.BUNDLE_NAME
        or item.offset != entries[0].offset
        or item.packed_size != entries[0].packed_length
        or item.unpacked_size != len(redswf)
        or store.bundles[0][1] + store.bundles[0][2] != bundle_path.stat().st_size
    ):
        raise ModInstallError("metadata.store does not describe the packed bundle")


def build_package(
    variant: str = "ai",
    *,
    checkpoint: Path = DEFAULT_CHECKPOINT,
    tier_checkpoints: list[str] | None = None,
    easy_temperature: float = 0.0,
) -> Path:
    if variant not in VARIANTS:
        raise ModInstallError(f"unknown mod variant: {variant}")
    command = cli_encoding.python_command(ROOT / "scripts/build_mod.py")
    if variant == "recorder":
        command.append("--recorder")
    else:
        if variant == "probe":
            command.append("--probe")
        command.extend(["--checkpoint", str(checkpoint)])
        for assignment in tier_checkpoints or []:
            command.extend(["--tier-checkpoint", assignment])
        command.extend(["--easy-temperature", str(easy_temperature)])
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        raise ModInstallError(
            f"offline mod build failed:\n{result.stdout}\n{result.stderr}"
        )
    return assemble_package(variant)


def resolve_package(path: Path) -> Path:
    """Accept an assembled package as is; assemble a finished build into its package folder."""
    path = path.expanduser().resolve()
    if (path / "package-manifest.json").is_file():
        return path
    manifest = path / "manifest.json"
    if not manifest.is_file():
        raise ModInstallError(
            f"{path} holds neither package-manifest.json nor a build manifest.json"
        )
    variant = json.loads(manifest.read_text(encoding="utf-8")).get("variant")
    if variant not in VARIANTS:
        raise ModInstallError(f"{manifest}: unknown variant {variant!r}")
    return assemble_package(variant, build_dir=path)


def _read_package(
    package_dir: Path,
) -> tuple[dict[str, Any], list[tuple[PurePosixPath, Path, str]]]:
    manifest = json.loads(
        (package_dir / "package-manifest.json").read_text(encoding="utf-8")
    )
    if (
        manifest.get("format") != PACKAGE_FORMAT
        or manifest.get("variant") not in VARIANTS
    ):
        raise ModInstallError(f"{package_dir}: unknown package format or variant")
    files = []
    for relative, info in sorted(manifest["files"].items()):
        source = package_dir.joinpath(*_safe_relative(relative).parts)
        if not source.is_file() or _sha256(source) != info["sha256"]:
            raise ModInstallError(f"package file is missing or damaged: {relative}")
        files.append((_safe_relative(relative), source, info["sha256"]))
    on_disk = {
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*")
        if path.is_file()
        and path.relative_to(package_dir).parts[0] in (MODS_DIR, "bin")
    }
    if on_disk != set(manifest["files"]):
        raise ModInstallError(
            f"package holds files its manifest does not list: {sorted(on_disk - set(manifest['files']))}"
        )
    return manifest, files


# --- game folder discovery -------------------------------------------------------------


def steam_roots(home: Path) -> list[Path]:
    return [
        home / ".steam/steam",
        home / ".local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
    ]


def steam_libraries(root: Path) -> list[Path]:
    libraries = [root]
    vdf = root / "steamapps/libraryfolders.vdf"
    if vdf.is_file():
        for match in re.finditer(
            r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")
        ):
            libraries.append(Path(match.group(1).replace("\\\\", "\\")))
    return libraries


def find_game_dirs(home: Path | None = None) -> list[Path]:
    found: dict[Path, None] = {}
    for root in steam_roots(home or Path.home()):
        for library in steam_libraries(root):
            candidate = library / "steamapps/common" / GAME_FOLDER
            if (candidate / "content/content0").is_dir():
                found.setdefault(candidate.resolve(), None)
    return list(found)


def proton_documents(game_dir: Path) -> Path:
    """The game's Documents folder inside its Proton prefix, in the same Steam library."""
    steamapps = game_dir.resolve().parent.parent
    return (
        steamapps
        / f"compatdata/{STEAM_APP_ID}/pfx/drive_c/users/steamuser/Documents/The Witcher 3"
    )


def detect_game_dir() -> Path:
    found = find_game_dirs()
    if len(found) == 1:
        return found[0]
    if not found:
        raise ModInstallError(
            "no Steam install of The Witcher 3 found; pass --game-dir"
        )
    raise ModInstallError(
        "several installs found; pass --game-dir: " + ", ".join(map(str, found))
    )


# --- preflight ---------------------------------------------------------------------------


@dataclass
class Preflight:
    game_dir: Path
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    observed: list[dict[str, Any]] = field(default_factory=list)
    exe_version: str | None = None
    mods_dir: str = MODS_DIR

    @property
    def ok(self) -> bool:
        return not self.problems

    def text(self) -> str:
        lines = [
            f"Game folder: {self.game_dir}",
            f"Game version: {self.exe_version or 'not found'}",
        ]
        lines += [f"REFUSED: {problem}" for problem in self.problems]
        lines += [f"note: {note}" for note in self.notes]
        lines.append(
            "Check passed." if self.ok else "Check failed; nothing was installed."
        )
        return "\n".join(lines)


def _exe_version(game_dir: Path) -> tuple[str | None, str]:
    for relative, renderer in (
        ("bin/x64_dx12/witcher3.exe", "DirectX 12"),
        ("bin/x64/witcher3.exe", "DirectX 11"),
    ):
        exe = game_dir / relative
        if exe.is_file():
            match = EXE_VERSION_RE.search(exe.read_bytes())
            return (
                f"{match.group(1).decode()} (P4CL {match.group(2).decode()})"
                if match
                else None
            ), renderer
    return None, "none"


def _mods_folder(game_dir: Path) -> list[str]:
    return sorted(
        child.name
        for child in game_dir.iterdir()
        if child.is_dir() and child.name.lower() == "mods"
    )


def preflight(
    game_dir: Path,
    pins: dict[str, Any],
    mod_name: str,
    *,
    installed_mod: str | None = None,
) -> Preflight:
    if installed_mod is not None and (
        installed_mod != mod_name or installed_mod.lower() not in OUR_MOD_NAMES
    ):
        raise ModInstallError(
            "preparation can retain only the same installed mod edition"
        )
    game_dir = game_dir.expanduser().resolve()
    check = Preflight(game_dir)
    if not (game_dir / "content/content0").is_dir():
        check.problems.append(
            f"{game_dir} is not a Witcher 3 game folder (no content/content0)"
        )
        return check
    target = pins["target"]
    version, renderer = _exe_version(game_dir)
    check.exe_version = version
    if renderer != "DirectX 12":
        check.notes.append(
            f"no bin/x64_dx12/witcher3.exe ({renderer}); the mod was measured on {target['title']} "
            f"{target['patch']}, which is DirectX 12 only"
        )
    elif version and not version.startswith(target["exe_version"] + " "):
        check.notes.append(
            f"the exe reports {version}; the pins were measured on {target['exe_version']}"
        )

    member_pins: dict[str, list[dict[str, Any]]] = {}
    for pin in pins["bundle_members"]:
        member_pins.setdefault(pin["bundle"], []).append(pin)
    for bundle_relative, bundle_pins in member_pins.items():
        bundle_path = game_dir / bundle_relative
        if not bundle_path.is_file():
            check.problems.append(
                f"{bundle_relative} is missing; this is not a complete {target['title']} install"
            )
            continue
        try:
            entries = {
                entry.name.lower(): entry
                for entry in extract_game.parse_bundle(bundle_path)
            }
        except extract_game.ExtractionError as exc:
            check.problems.append(
                f"{bundle_relative} cannot be read as a 5.01 bundle ({exc}); a different game version"
            )
            continue
        all_members_match = True
        for pin in bundle_pins:
            entry = entries.get(pin["member"].lower())
            actual = (
                None
                if entry is None
                else hashlib.sha256(
                    extract_game.read_entry(bundle_path, entry)
                ).hexdigest()
            )
            check.observed.append(
                {"path": f"{bundle_relative}::{pin['member']}", "sha256": actual}
            )
            if actual == pin["sha256"]:
                continue
            all_members_match = False
            found = (
                "is missing"
                if actual is None
                else f"differs (sha256 {actual[:16]}..., expected {pin['sha256'][:16]}...)"
            )
            if pin["role"] == "replaced":
                meaning = (
                    "The game's Gwent screen is not the one the mod was built from, so installing would put an "
                    "older patched copy over it. This game is probably a different patch than "
                    f"{target['patch']} ({target['exe_version']}). Rebuild the mod from this game's files "
                    "(scripts/extract_game.py, then scripts/build_mod.py)."
                )
            else:
                meaning = (
                    "The card data differs from what the network was trained on. Either a different patch, or "
                    "a mod changed the game's own bundle in place (verify the game files in Steam)."
                )
            check.problems.append(
                f"{bundle_relative} member {pin['member']} {found}. {meaning}"
            )
        seen = pins.get("bundles_seen", {}).get(Path(bundle_relative).name)
        if all_members_match and seen and _sha256(bundle_path) != seen["sha256"]:
            check.notes.append(
                f"{bundle_relative} differs from the measured {target['patch']} bundle, but every pinned file "
                "inside it is identical; other files in it changed"
            )

    for pin in pins["files"]:
        path = game_dir / pin["path"]
        actual = _sha256(path) if path.is_file() else None
        check.observed.append({"path": pin["path"], "sha256": actual})
        if actual == pin["sha256"]:
            continue
        found = (
            "is missing"
            if actual is None
            else f"differs (sha256 {actual[:16]}..., expected {pin['sha256'][:16]}...)"
        )
        check.problems.append(
            f"{pin['path']} {found}. {pin['why']} Either this is a different patch than {target['patch']}, or a "
            "mod overwrote the vanilla script in place instead of living in the mods folder (verify the game "
            "files in Steam). With a different file the scripts can fail to compile at launch."
        )

    menu_dir = game_dir / MENU_DIR
    if not menu_dir.is_dir():
        check.problems.append(
            f"{MENU_DIR} is missing; the Options > Mods menu would never appear"
        )
    else:
        present = [name for name in FILELIST_NAMES if (menu_dir / name).is_file()]
        if present:
            check.notes.append(
                f"{' and '.join(present)} found (a 4.x layout); the installer adds one line to each. "
                "5.01 has no filelists and reads every .xml in the folder"
            )

    mods = _mods_folder(game_dir)
    if len(mods) > 1:
        check.problems.append(
            f"both {' and '.join(mods)} exist in the game folder. On Linux, Proton reads only one of them; "
            "move the mods into one folder first"
        )
        return check
    check.mods_dir = mods[0] if mods else MODS_DIR
    _check_other_mods(check, game_dir, pins, mod_name, installed_mod=installed_mod)
    return check


def _check_other_mods(
    check: Preflight,
    game_dir: Path,
    pins: dict[str, Any],
    mod_name: str,
    *,
    installed_mod: str | None = None,
) -> None:
    folders = []
    mods_root = game_dir / check.mods_dir
    if mods_root.is_dir():
        folders += [
            path
            for path in sorted(mods_root.iterdir())
            if path.is_dir() and path.name.lower().startswith("mod")
        ]
    dlc_root = game_dir / "dlc"
    if dlc_root.is_dir():
        folders += [path for path in sorted(dlc_root.iterdir()) if path.is_dir()]
    script_paths = {
        PurePosixPath(pin["path"])
        .relative_to("content/content0")
        .as_posix()
        .lower(): pin["path"]
        for pin in pins["files"]
    }
    for folder in folders:
        label = folder.relative_to(game_dir).as_posix()
        if folder.parent == mods_root and folder.name.lower() in OUR_MOD_NAMES:
            if folder.name == installed_mod:
                check.notes.append(
                    f"{label} retained during preparation; installation is unchanged"
                )
                continue
            check.problems.append(
                f"{label} is already present. If this tool installed it, run --uninstall first; "
                "otherwise delete that folder (one edition of the mod at a time)"
            )
            continue
        content = next(
            (
                child
                for child in folder.iterdir()
                if child.is_dir() and child.name.lower() == "content"
            ),
            None,
        )
        if content is None:
            continue
        for item in sorted(content.rglob("*")):
            relative = item.relative_to(content).as_posix().lower()
            if item.is_file() and item.suffix.lower() == ".bundle":
                try:
                    members = redbundle.bundle_member_names(item)
                except (OSError, redbundle.BundleFormatError) as exc:
                    check.notes.append(f"{label}: cannot read {item.name} ({exc})")
                    continue
                if SWF_MEMBER in members:
                    order = (
                        "before" if folder.name.lower() < mod_name.lower() else "after"
                    )
                    check.problems.append(
                        f"{label} also replaces gwint_game.redswf (in {item.name}). Only one mod can own the "
                        f"Gwent screen; the game uses the mod that loads first, alphabetical unless "
                        f"mods.settings sets priorities, and {folder.name} sorts {order} {mod_name}. "
                        "Uninstall or disable it first"
                    )
            elif item.is_file() and relative in script_paths:
                check.notes.append(
                    f"{label} overrides {script_paths[relative]}; the mod's wrapper runs on top of it, but if "
                    "that copy lacks OnConfigUI the scripts fail to compile"
                )


# --- install and uninstall ---------------------------------------------------------------


def _game_path(relative: PurePosixPath, mods_dir: str) -> PurePosixPath:
    if relative.parts[0] == MODS_DIR:
        return PurePosixPath(mods_dir, *relative.parts[1:])
    return relative


def _ensure_parent(
    game_dir: Path, relative: PurePosixPath, created_dirs: list[str]
) -> None:
    parent = game_dir
    current = PurePosixPath()
    for part in relative.parts[:-1]:
        current /= part
        parent /= part
        if not parent.exists():
            parent.mkdir()
            created_dirs.append(current.as_posix())
        elif not parent.is_dir():
            raise ModInstallError(f"install parent is not a directory: {current}")


def _remove_created_dirs(game_dir: Path, directories: list[str]) -> None:
    for value in reversed(directories):
        try:
            game_dir.joinpath(*_safe_relative(value).parts).rmdir()
        except OSError:
            pass


def _remove_filelist_line(target: Path, line: str) -> None:
    if not target.is_file():
        return
    wanted = line.encode("utf-8").rstrip(b"\r\n")
    lines = target.read_bytes().splitlines(keepends=True)
    matches = sum(candidate.rstrip(b"\r\n") == wanted for candidate in lines)
    if matches > 1:
        raise ModInstallError(
            f"filelist line is ambiguous during uninstall: {target.name}"
        )
    if matches == 1:
        target.write_bytes(
            b"".join(
                candidate for candidate in lines if candidate.rstrip(b"\r\n") != wanted
            )
        )


def _invalidate_script_cache(
    game_dir: Path, archive_dir: Path
) -> dict[str, Any] | None:
    """Archive the generated cache so startup compiles the current mod set."""
    cache = game_dir.joinpath(*SCRIPT_CACHE.parts)
    if not cache.exists():
        return None
    if cache.is_symlink() or not cache.is_file():
        raise ModInstallError(f"generated script cache is not a regular file: {cache}")
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / SCRIPT_CACHE.name
    digest = _sha256(cache)
    shutil.copy2(cache, archive)
    if _sha256(archive) != digest:
        raise ModInstallError("generated script cache archive verification failed")
    cache.unlink()
    return {"path": SCRIPT_CACHE.as_posix(), "sha256": digest, "archive": str(archive)}


def install_package(
    game_dir: Path, *, package_dir: Path, runtime_dir: Path = RUNTIME_DIR
) -> Path:
    """Check the game, back up what the package replaces, copy it, and write the receipt."""
    game_dir = game_dir.expanduser().resolve()
    package_dir = package_dir.resolve()
    runtime_dir = runtime_dir.resolve()
    receipt_path = runtime_dir / RECEIPT_NAME
    if receipt_path.exists():
        raise ModInstallError(
            f"an install receipt already exists ({receipt_path}); run --uninstall first"
        )
    if not game_dir.is_dir():
        raise ModInstallError(f"game directory must already exist: {game_dir}")
    manifest, package_files = _read_package(package_dir)
    pins = load_pins(package_dir / "game-pins.json")
    check = preflight(game_dir, pins, manifest["mod_name"])
    if not check.ok:
        raise ModInstallError(check.text())

    plan = [
        (_game_path(relative, check.mods_dir), source, digest)
        for relative, source, digest in package_files
    ]
    for relative, _, _ in plan:
        target = game_dir.joinpath(*relative.parts)
        if target.exists() and not target.is_file():
            raise ModInstallError(f"install target is not a regular file: {relative}")
    menu_line = PurePosixPath(VARIANTS[manifest["variant"]].menu).name + ";"
    filelist_edits = []
    for name in FILELIST_NAMES:
        target = game_dir / MENU_DIR / name
        if target.is_file():
            original = target.read_bytes()
            if menu_line.encode("utf-8") not in original.splitlines():
                filelist_edits.append((MENU_DIR / name, original))

    stamp = datetime.now().astimezone()
    backup_dir = runtime_dir / f"mod-backup-{stamp.strftime('%Y%m%d-%H%M%S-%f')}"
    require_game_stopped()
    backup_dir.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = []
    created_dirs: list[str] = []
    recorded_edits: list[dict[str, Any]] = []
    cache_archive = None
    try:
        for relative, source, digest in plan:
            target = game_dir.joinpath(*relative.parts)
            entry: dict[str, Any] = {
                "path": relative.as_posix(),
                "existed": target.is_file(),
                "original_sha256": None,
            }
            if entry["existed"]:
                entry["original_sha256"] = _sha256(target)
                backup = backup_dir.joinpath(*relative.parts)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            entries.append(entry)
            _ensure_parent(game_dir, relative, created_dirs)
            shutil.copy2(source, target)
            entry["installed_sha256"] = _sha256(target)
            entry["size"] = target.stat().st_size
            if entry["installed_sha256"] != digest:
                raise ModInstallError(
                    f"copied file differs from the package: {relative}"
                )
        for relative, original in filelist_edits:
            target = game_dir.joinpath(*relative.parts)
            newline = b"\r\n" if b"\r\n" in original else b"\n"
            updated = (
                original
                if not original or original.endswith((b"\n", b"\r"))
                else original + newline
            )
            installed = updated + menu_line.encode("utf-8") + newline
            recorded_edits.append(
                {
                    "path": relative.as_posix(),
                    "line": menu_line,
                    "original_hex": original.hex(),
                    "original_sha256": hashlib.sha256(original).hexdigest(),
                    "installed_sha256": hashlib.sha256(installed).hexdigest(),
                }
            )
            target.write_bytes(installed)
        cache_archive = _invalidate_script_cache(
            game_dir, backup_dir / "script-cache-before-install"
        )
        _write_json(
            receipt_path,
            {
                "format": RECEIPT_FORMAT,
                "installed_at": stamp.isoformat(timespec="seconds"),
                "variant": manifest["variant"],
                "mod_name": manifest["mod_name"],
                "game_dir": str(game_dir),
                "game_version": check.exe_version,
                "game_files_checked": check.observed,
                "package_dir": str(package_dir),
                "package_manifest_sha256": _sha256(
                    package_dir / "package-manifest.json"
                ),
                "backup_dir": backup_dir.name,
                "files": entries,
                "filelist_edits": recorded_edits,
                "created_dirs": created_dirs,
                "invalidated_script_cache": cache_archive,
            },
        )
    except Exception:
        if cache_archive is not None:
            shutil.copy2(
                cache_archive["archive"], game_dir.joinpath(*SCRIPT_CACHE.parts)
            )
        for entry in reversed(entries):
            target = game_dir.joinpath(*_safe_relative(entry["path"]).parts)
            if entry["existed"]:
                shutil.copy2(
                    backup_dir.joinpath(*_safe_relative(entry["path"]).parts), target
                )
            elif target.is_file():
                target.unlink()
        for edit in recorded_edits:
            game_dir.joinpath(*_safe_relative(edit["path"]).parts).write_bytes(
                bytes.fromhex(edit["original_hex"])
            )
        _remove_created_dirs(game_dir, created_dirs)
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise
    return receipt_path


def receipt_game_dir(runtime_dir: Path) -> str:
    receipt_path = runtime_dir / RECEIPT_NAME
    if not receipt_path.is_file():
        raise ModInstallError(f"no install receipt to restore from ({receipt_path})")
    return json.loads(receipt_path.read_text(encoding="utf-8"))["game_dir"]


def uninstall_package(game_dir: Path, *, runtime_dir: Path = RUNTIME_DIR) -> Path:
    """Restore every file the receipt names; refuse if any installed file was changed since."""
    game_dir = game_dir.expanduser().resolve()
    runtime_dir = runtime_dir.resolve()
    receipt_path = runtime_dir / RECEIPT_NAME
    if not receipt_path.is_file():
        raise ModInstallError(f"no install receipt to restore from ({receipt_path})")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("format") != RECEIPT_FORMAT:
        raise ModInstallError("install receipt has an unknown format")
    if str(game_dir) != receipt.get("game_dir"):
        raise ModInstallError(
            f"game directory does not match the receipt ({receipt.get('game_dir')})"
        )
    backup_name = receipt.get("backup_dir")
    if (
        not isinstance(backup_name, str)
        or Path(backup_name).name != backup_name
        or not backup_name.startswith("mod-backup-")
    ):
        raise ModInstallError("install receipt has an unsafe backup path")
    backup_dir = runtime_dir / backup_name
    for entry in receipt["files"]:
        relative = _safe_relative(entry["path"])
        target = game_dir.joinpath(*relative.parts)
        if not target.is_file() or _sha256(target) != entry["installed_sha256"]:
            raise ModInstallError(
                f"installed target changed since install; refusing to restore: {relative}"
            )
        if entry["existed"]:
            backup = backup_dir.joinpath(*relative.parts)
            if not backup.is_file() or _sha256(backup) != entry["original_sha256"]:
                raise ModInstallError(f"backup file is missing or changed: {relative}")
    require_game_stopped()
    _invalidate_script_cache(game_dir, backup_dir / "script-cache-before-uninstall")
    for entry in receipt["files"]:
        relative = _safe_relative(entry["path"])
        target = game_dir.joinpath(*relative.parts)
        if entry["existed"]:
            shutil.copy2(backup_dir.joinpath(*relative.parts), target)
        else:
            target.unlink()
    for edit in receipt["filelist_edits"]:
        target = game_dir.joinpath(*_safe_relative(edit["path"]).parts)
        if target.is_file() and _sha256(target) == edit["installed_sha256"]:
            target.write_bytes(bytes.fromhex(edit["original_hex"]))
        else:
            _remove_filelist_line(target, edit["line"])
    _remove_created_dirs(game_dir, receipt["created_dirs"])
    receipt_path.unlink()
    return backup_dir


# --- first-match report ------------------------------------------------------------------


REPORT_SECTIONS = (
    "W3GwentAI",
    "W3GwentAIDiagnostics",
    "W3GwentAIFun",
    "W3GwentAIStats",
    "W3GwentTimingProbe",
    "W3GwentRecorder",
)


def settings_sections(
    path: Path, names: tuple[str, ...] = REPORT_SECTIONS
) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        header = re.fullmatch(r"\s*\[([^\]]+)\]\s*", line)
        if header:
            current = header.group(1) if header.group(1) in names else None
            if current:
                sections.setdefault(current, [])
        elif current and line.strip():
            sections[current].append(line)
    return sections


def first_match_report(
    game_dir: Path, documents: Path, runtime_dir: Path = RUNTIME_DIR
) -> str:
    lines = [f"Game folder: {game_dir}", f"Documents folder: {documents}"]
    cache = game_dir.joinpath(*SCRIPT_CACHE.parts)
    if cache.is_file():
        current = b"OnW3GwentAIDiagnostic" in cache.read_bytes()
        lines.append(
            f"Compiled script cache: modified {datetime.fromtimestamp(cache.stat().st_mtime).isoformat(timespec='seconds')}; diagnostic callback present: {current}"
        )
    else:
        lines.append("Compiled script cache: absent; next launch must compile")
    receipt_path = runtime_dir / RECEIPT_NAME
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        lines.append(
            f"Installed: {receipt['mod_name']} ({receipt['variant']}) at {receipt['installed_at']}, "
            f"game {receipt['game_version']}, {len(receipt['files'])} files"
        )
    else:
        lines.append("Installed: no receipt in this checkout")
    for name in ("dx12user.settings", "user.settings"):
        path = documents / name
        if not path.is_file():
            lines.append(f"{name}: not present")
            continue
        sections = settings_sections(path)
        lines.append(
            f"{name}: modified {datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds')}"
        )
        for section in REPORT_SECTIONS:
            if section in sections:
                lines.append(f"[{section}]")
                lines += sections[section]
    for name in ("scriptslog.txt", "mods.settings"):
        path = documents / name
        if path.is_file():
            lines.append(f"--- {name} (last 40 lines)")
            lines += path.read_text(encoding="utf-8", errors="replace").splitlines()[
                -40:
            ]
    return "\n".join(lines)


# --- command line ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    cli_encoding.configure_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--package",
        type=Path,
        help="install this build (manifest.json) or assembled package",
    )
    actions.add_argument(
        "--uninstall",
        action="store_true",
        help="restore the game folder from the receipt",
    )
    actions.add_argument(
        "--report",
        action="store_true",
        help="print what to send back after the first match",
    )
    actions.add_argument(
        "--build",
        action="store_true",
        help="run the offline build, then assemble the package",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="with --package: check the game and install nothing",
    )
    parser.add_argument(
        "--game-dir",
        type=Path,
        help="game root holding bin and content (default: detect Steam)",
    )
    parser.add_argument(
        "--documents",
        type=Path,
        help="Documents/The Witcher 3 folder for --report (default: Proton prefix)",
    )
    parser.add_argument(
        "--variant", choices=tuple(VARIANTS), default="ai", help="edition for --build"
    )
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument(
        "--tier-checkpoint", action="append", default=[], metavar="TIER=PATH"
    )
    parser.add_argument("--easy-temperature", type=float, default=0.0)
    args = parser.parse_args(argv)
    if args.check and args.package is None:
        parser.error("--check needs --package")
    try:
        if args.build:
            package = build_package(
                args.variant,
                checkpoint=args.checkpoint,
                tier_checkpoints=args.tier_checkpoint,
                easy_temperature=args.easy_temperature,
            )
            print(f"Package assembled at {package}")
            return 0
        if args.uninstall:
            game_dir = args.game_dir or Path(receipt_game_dir(RUNTIME_DIR))
            backup = uninstall_package(game_dir)
            print(f"Restored {game_dir} from the receipt; backups kept in {backup}")
            return 0
        game_dir = (args.game_dir or detect_game_dir()).expanduser().resolve()
        if args.report:
            print(
                first_match_report(
                    game_dir, args.documents or proton_documents(game_dir)
                )
            )
            return 0
        package = resolve_package(args.package)
        manifest, _ = _read_package(package)
        if args.check:
            check = preflight(
                game_dir, load_pins(package / "game-pins.json"), manifest["mod_name"]
            )
            print(check.text())
            return 0 if check.ok else 1
        receipt = install_package(game_dir, package_dir=package)
        print(install_summary(receipt))
        return 0
    except ModInstallError as exc:
        print(exc, file=sys.stderr)
        return 1


def install_summary(receipt_path: Path) -> str:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    lines = [
        f"Installed {receipt['mod_name']} into {receipt['game_dir']} (game {receipt['game_version']}):"
    ]
    lines += [f"  {entry['path']}" for entry in receipt["files"]]
    lines += [
        f"  {edit['path']} (+ {edit['line']})" for edit in receipt["filelist_edits"]
    ]
    lines.append(f"Receipt: {receipt_path}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
