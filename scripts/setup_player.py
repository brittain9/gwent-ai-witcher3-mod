#!/usr/bin/env python3
"""Prepare and install A Worthy Opponent from a player download."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

sys.dont_write_bytecode = True

try:
    from scripts import cli_encoding
except ModuleNotFoundError:  # direct script invocation
    import cli_encoding

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FORMAT = "witcher3-player-release-v1"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 30_000
FFDEC_VERSION = "26.4.0"
NODE_VERSION = "24.11.1"
JAVA_VERSION = "21.0.13+9"
JAVA_SOURCE_MARKER = "WORTHY_OPPONENT_JAVA_SOURCE_OK été 日本語"


class SetupError(RuntimeError):
    """A preparation step could not complete safely."""


@dataclass(frozen=True)
class ToolArchive:
    name: str
    version: str
    url: str
    sha256: str
    executable: str
    kind: str
    prefix: str


# Archive checksums cover the complete official distribution, including libraries.
# Sources: JPEXS release assets, Node SHASUMS256.txt, Temurin release asset digests.
FFDEC = ToolArchive(
    "ffdec",
    FFDEC_VERSION,
    "https://github.com/jindrapetrik/jpexs-decompiler/releases/download/"
    "version26.4.0/ffdec_26.4.0.zip",
    "56ce960aeafc7caf034e2052ba49f878a883f7c7ca690da6eeffca9587b5e4b9",
    "ffdec-cli.jar",
    "zip",
    "",
)
TOOL_ARCHIVES = {
    "windows": {
        "node": ToolArchive(
            "node",
            NODE_VERSION,
            "https://nodejs.org/dist/v24.11.1/node-v24.11.1-win-x64.zip",
            "5355ae6d7c49eddcfde7d34ac3486820600a831bf81dc3bdca5c8db6a9bb0e76",
            "node.exe",
            "zip",
            "node-v24.11.1-win-x64",
        ),
        "java": ToolArchive(
            "java",
            JAVA_VERSION,
            "https://github.com/adoptium/temurin21-binaries/releases/download/"
            "jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_windows_hotspot_21.0.13_9.zip",
            "a4dbaa05fca4bd89d84584e58c459bd732efebb1722af80ab13c6516832004f9",
            "bin/java.exe",
            "zip",
            "jdk-21.0.13+9",
        ),
    },
    "linux": {
        "node": ToolArchive(
            "node",
            NODE_VERSION,
            "https://nodejs.org/dist/v24.11.1/node-v24.11.1-linux-x64.tar.xz",
            "60e3b0a8500819514aca603487c254298cd776de0698d3cd08f11dba5b8289a8",
            "bin/node",
            "tar.xz",
            "node-v24.11.1-linux-x64",
        ),
        "java": ToolArchive(
            "java",
            JAVA_VERSION,
            "https://github.com/adoptium/temurin21-binaries/releases/download/"
            "jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz",
            "d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c",
            "bin/java",
            "tar.gz",
            "jdk-21.0.13+9",
        ),
    },
}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\0" in normalized
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in normalized.rstrip("/").split("/"))
        or any(":" in part for part in path.parts)
    ):
        raise SetupError(f"Unsafe archive or inventory path: {name!r}")
    return path


def _target(root: Path, name: str) -> Path:
    target = root.joinpath(*_relative(name).parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise SetupError(f"Archive path escapes its output folder: {name!r}")
    return target


def extract_tool_archive(archive: Path, output: Path) -> None:
    """Extract verified tool files without device entries or escaping links."""
    output.mkdir(parents=True, exist_ok=False)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as source:
            entries = source.infolist()
            if (
                len(entries) > MAX_ARCHIVE_FILES
                or sum(x.file_size for x in entries) > MAX_EXTRACTED_BYTES
            ):
                raise SetupError("Tool archive exceeds the extraction limit")
            names = set()
            for entry in entries:
                target = _target(output, entry.filename)
                if target in names:
                    raise SetupError(f"Duplicate tool archive path: {entry.filename}")
                names.add(target)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise SetupError(
                        f"ZIP tool archive contains a symlink: {entry.filename}"
                    )
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as handle, target.open("xb") as destination:
                    shutil.copyfileobj(handle, destination)
                target.chmod(0o755 if mode & 0o111 else 0o644)
        return
    with tarfile.open(archive, "r:*") as source:
        entries = source.getmembers()
        if (
            len(entries) > MAX_ARCHIVE_FILES
            or sum(x.size for x in entries) > MAX_EXTRACTED_BYTES
        ):
            raise SetupError("Tool archive exceeds the extraction limit")
        names = set()
        links = []
        for entry in entries:
            target = _target(output, entry.name)
            if target in names:
                raise SetupError(f"Duplicate tool archive path: {entry.name}")
            names.add(target)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with (
                    source.extractfile(entry) as handle,
                    target.open("xb") as destination,
                ):
                    shutil.copyfileobj(handle, destination)
                target.chmod(0o755 if entry.mode & 0o111 else 0o644)
            elif entry.issym() or entry.islnk():
                if "\0" in entry.linkname or "\\" in entry.linkname:
                    raise SetupError(f"Unsafe tool archive link: {entry.name}")
                link = PurePosixPath(entry.linkname)
                if link.is_absolute() or any(":" in part for part in link.parts):
                    raise SetupError(f"Unsafe tool archive link: {entry.name}")
                linked = (
                    (target.parent / entry.linkname)
                    if entry.issym()
                    else (output / entry.linkname)
                )
                if not linked.resolve().is_relative_to(output.resolve()):
                    raise SetupError(
                        f"Tool archive link escapes its output: {entry.name}"
                    )
                links.append((target, linked, entry.issym(), entry.linkname))
            else:
                raise SetupError(f"Unsupported tool archive member: {entry.name}")
        for target, linked, symbolic, linkname in links:
            target.parent.mkdir(parents=True, exist_ok=True)
            if symbolic:
                target.symlink_to(linkname)
            else:
                if not linked.is_file() or linked.is_symlink():
                    raise SetupError(f"Invalid tool hard link: {target.name}")
                shutil.copyfile(linked, target)
        for target, _, _, _ in links:
            if (
                not target.resolve().is_relative_to(output.resolve())
                or not target.exists()
            ):
                raise SetupError(f"Tool archive has an invalid link: {target.name}")


def download_file(url: str, target: Path) -> None:
    if urllib.parse.urlparse(url).scheme != "https":
        raise SetupError("Tool downloads require HTTPS")
    request = urllib.request.Request(
        url, headers={"User-Agent": "A-Worthy-Opponent/0.1.0"}
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as source,
            target.open("xb") as destination,
        ):
            if urllib.parse.urlparse(source.geturl()).scheme != "https":
                raise SetupError("Tool download redirected away from HTTPS")
            written = 0
            while block := source.read(1024 * 1024):
                written += len(block)
                if written > MAX_ARCHIVE_BYTES:
                    raise SetupError("Tool download exceeds the size limit")
                destination.write(block)
    except (OSError, urllib.error.URLError) as exc:
        raise SetupError(f"Could not download {url}: {exc}") from exc


def _inventory(root: Path) -> dict[str, dict[str, object]]:
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == "_tool-receipt.json":
            continue
        if path.is_symlink():
            if not path.resolve().is_relative_to(root.resolve()) or not path.exists():
                raise SetupError(f"Tool cache link escapes or is missing: {relative}")
            files[relative] = {"symlink": os.readlink(path)}
        elif path.is_file():
            files[relative] = {"sha256": file_hash(path), "bytes": path.stat().st_size}
    return files


def _archive_inventory(
    archive: Path, spec: ToolArchive
) -> dict[str, dict[str, object]]:
    """Derive expected cache hashes from the pinned archive, not its local receipt."""
    files = {}

    def relative(name: str) -> str | None:
        path = _relative(name)
        if spec.prefix:
            prefix = PurePosixPath(spec.prefix)
            if path == prefix:
                return None
            if not path.is_relative_to(prefix):
                raise SetupError(f"Unexpected top-level path in {spec.name} archive")
            path = path.relative_to(prefix)
        return path.as_posix()

    def record(name: str, handle, size: int) -> None:
        key = relative(name)
        if key is None:
            return
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
        files[key] = {"sha256": digest.hexdigest(), "bytes": size}

    if spec.kind == "zip":
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                if not item.is_dir():
                    with source.open(item) as handle:
                        record(item.filename, handle, item.file_size)
    else:
        with tarfile.open(archive, "r:*") as source:
            for item in source.getmembers():
                if item.isfile() or item.islnk():
                    with source.extractfile(item) as handle:
                        size = item.size if item.isfile() else handle.seek(0, 2)
                        handle.seek(0)
                        record(item.name, handle, size)
                elif item.issym():
                    key = relative(item.name)
                    if key is not None:
                        files[key] = {"symlink": item.linkname}
    return files


def ensure_tool(
    spec: ToolArchive,
    tools_dir: Path,
    *,
    download: Callable[[str, Path], object] = download_file,
    offline: bool = False,
) -> Path:
    destination = tools_dir / spec.name
    archive_cache = tools_dir / "archives" / f"{spec.sha256}.{spec.kind}"
    if archive_cache.is_symlink() or archive_cache.parent.is_symlink():
        raise SetupError("The archive cache must not be a symlink")
    if destination.is_symlink():
        raise SetupError(f"Tool cache {destination.name} is a symlink")
    if destination.exists() or destination.is_symlink():
        receipt_path = destination / "_tool-receipt.json"
        try:
            if receipt_path.is_symlink() or archive_cache.is_symlink():
                raise SetupError(
                    f"Tool cache {destination.name} contains an unexpected symlink"
                )
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            valid = (
                receipt["archive_sha256"] == spec.sha256
                and receipt["version"] == spec.version
                and archive_cache.is_file()
                and file_hash(archive_cache) == spec.sha256
                and receipt["files"] == _archive_inventory(archive_cache, spec)
                and receipt["files"] == _inventory(destination)
                and (destination / spec.executable).is_file()
            )
        except (OSError, ValueError, KeyError):
            valid = False
        if not valid:
            raise SetupError(
                f"Tool cache {destination.name} is unverified or changed. "
                "Keep it for inspection and extract the release into a new folder."
            )
        return destination / spec.executable
    cached = archive_cache.exists()
    if cached and (
        not archive_cache.is_file() or file_hash(archive_cache) != spec.sha256
    ):
        raise SetupError(f"The cached {spec.name} archive changed (SHA-256 mismatch)")
    if offline and not cached:
        raise SetupError(
            f"{spec.name} {spec.version} is not cached; rerun without --offline"
        )
    tools_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"{spec.name}-download-", dir=tools_dir
    ) as value:
        temporary = Path(value)
        archive = temporary / ("tool." + spec.kind)
        if cached:
            print(
                f"Preparing {spec.name} {spec.version} from its verified archive...",
                flush=True,
            )
            shutil.copyfile(archive_cache, archive)
        else:
            print(
                f"Downloading {spec.name} {spec.version} from its official distribution...",
                flush=True,
            )
            download(spec.url, archive)
        if not archive.is_file() or file_hash(archive) != spec.sha256:
            raise SetupError(
                f"{spec.name} download SHA-256 does not match the pinned release"
            )
        extracted = temporary / "extracted"
        extract_tool_archive(archive, extracted)
        tool = extracted / spec.prefix if spec.prefix else extracted
        if not tool.is_dir() or not (tool / spec.executable).is_file():
            raise SetupError(f"{spec.name} archive does not contain {spec.executable}")
        receipt = {
            "version": spec.version,
            "archive_sha256": spec.sha256,
            "source_url": spec.url,
            "files": _inventory(tool),
        }
        archive_cache.parent.mkdir(parents=True, exist_ok=True)
        if archive_cache.parent.is_symlink() or archive_cache.is_symlink():
            raise SetupError("The archive cache must not be a symlink")
        if archive_cache.exists() and file_hash(archive_cache) != spec.sha256:
            raise SetupError(f"The cached {spec.name} archive changed")
        if not archive_cache.exists():
            with (
                archive_cache.open("xb") as destination_archive,
                archive.open("rb") as source_archive,
            ):
                shutil.copyfileobj(source_archive, destination_archive)
        (tool / "_tool-receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        )
        tool.rename(destination)
    return destination / spec.executable


def _source_compiler_available(java: Path) -> bool:
    marker = JAVA_SOURCE_MARKER
    try:
        with tempfile.TemporaryDirectory(prefix="worthy-opponent-java-") as value:
            source = Path(value) / "WorthyOpponentJavaCheck.java"
            source.write_text(
                "class WorthyOpponentJavaCheck {"
                " public static void main(String[] args) {"
                f' System.out.print("{marker}");'
                f' System.err.print("{marker}");'
                " } }\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    str(java),
                    *cli_encoding.JAVA_UTF8_OPTIONS,
                    "-Djava.awt.headless=true",
                    str(source),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=15,
                check=False,
            )
            return (
                result.returncode == 0
                and result.stdout == marker.encode("utf-8")
                and result.stderr == marker.encode("utf-8")
            )
    except (OSError, subprocess.SubprocessError):
        return False


def _system_tool(name: str, minimum: int) -> Path | None:
    executable = shutil.which(name)
    if executable is None:
        return None
    option = "-version" if name == "java" else "--version"
    try:
        result = subprocess.run(
            [
                executable,
                *(cli_encoding.JAVA_UTF8_OPTIONS if name == "java" else ()),
                option,
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    text = result.stdout + result.stderr
    match = re.search(r'(?:version\s+"|^v|javac\s+)(\d+)', text)
    if result.returncode or not match or int(match.group(1)) < minimum:
        return None
    resolved = Path(executable).resolve()
    if name == "java" and not _source_compiler_available(resolved):
        return None
    return resolved


def ensure_tools(root: Path, *, offline: bool = False) -> dict[str, str]:
    system = platform.system().lower()
    if system not in TOOL_ARCHIVES or platform.machine().lower() not in {
        "x86_64",
        "amd64",
    }:
        raise SetupError("This installer currently supports x64 Windows and x64 Linux")
    tools_dir = root / ".local/tools"
    java = _system_tool("java", 11)
    if java is None:
        java = ensure_tool(TOOL_ARCHIVES[system]["java"], tools_dir, offline=offline)
    node = _system_tool("node", 24)
    if node is None:
        node = ensure_tool(TOOL_ARCHIVES[system]["node"], tools_dir, offline=offline)
    ensure_tool(FFDEC, tools_dir, offline=offline)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        [str(java.parent), str(node.parent), env.get("PATH", "")]
    )
    return env


def verify_distribution(root: Path) -> None:
    manifest_path = root / "release-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(
            "Extract the complete player release ZIP before running setup"
        ) from exc
    if manifest.get("format") != RELEASE_FORMAT or not isinstance(
        manifest.get("files"), dict
    ):
        raise SetupError("The player release manifest is unsupported")
    for name, entry in manifest["files"].items():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"bytes", "sha256"}
            or type(entry["bytes"]) is not int
            or entry["bytes"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry["sha256"]))
        ):
            raise SetupError(f"Invalid release inventory entry: {name}")
        path = _target(root, name)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != entry.get("bytes")
            or file_hash(path) != entry.get("sha256")
        ):
            raise SetupError(f"Release file is missing or changed: {name}")
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if (
            relative.parts[0] in {".local", ".runtime"}
            or "__pycache__" in relative.parts
        ):
            continue
        if path.is_symlink() or (
            path.is_file()
            and relative.as_posix() not in manifest["files"]
            and relative.as_posix() != "release-manifest.json"
        ):
            raise SetupError(
                f"Unexpected file in player download: {relative.as_posix()}"
            )


def _installer():
    try:
        from scripts import install_mod
    except ImportError:
        import install_mod
    return install_mod


def _managed_install(root: Path, game_dir: Path) -> str | None:
    game_dir = game_dir.expanduser().resolve()
    receipt_path = root / ".runtime/mod-install-receipt.json"
    if not receipt_path.exists():
        return None
    installer = _installer()
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("format") != installer.RECEIPT_FORMAT
            or Path(receipt["game_dir"]).resolve() != game_dir
        ):
            raise SetupError(
                "The installation receipt belongs to a different game folder"
            )
        mod_name = receipt["mod_name"]
        if mod_name != "modW3GwentAI" or len(receipt["files"]) != 5:
            raise SetupError("The installation receipt is not for this mod edition")
        expected = {
            f"Mods/{mod_name}/content/blob0.bundle",
            f"Mods/{mod_name}/content/metadata.store",
            f"Mods/{mod_name}/content/scripts/local/gwai_settings.ws",
            f"Mods/{mod_name}/content/scripts/local/gwai_playtrace.ws",
            "bin/config/r4game/user_config_matrix/pc/modW3GwentAI.xml",
        }
        observed = set()
        for entry in receipt["files"]:
            relative = _relative(entry["path"])
            if (
                len(relative.parts) >= 3
                and relative.parts[0].lower() == "mods"
                and relative.parts[1] == mod_name
            ):
                normalized = PurePosixPath("Mods", *relative.parts[1:]).as_posix()
            else:
                normalized = relative.as_posix()
            if normalized not in expected:
                raise SetupError("The installation receipt names an unexpected file")
            if normalized in observed:
                raise SetupError("The installation receipt contains a duplicate file")
            observed.add(normalized)
            target = _target(game_dir, relative.as_posix())
            if (
                target.is_symlink()
                or not target.is_file()
                or file_hash(target) != entry["installed_sha256"]
            ):
                raise SetupError(f"Installed file changed: {relative}")
        if observed != expected:
            raise SetupError(
                "The installation receipt does not contain the expected files"
            )
        return mod_name
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SetupError(
            f"Cannot validate the existing installation receipt: {exc}"
        ) from exc


def check_game(
    root: Path, game_dir: Path, *, installed_from: Path | None = None
) -> None:
    installer = _installer()
    receipt_root = (
        root if installed_from is None else installed_from.expanduser().resolve()
    )
    managed = _managed_install(receipt_root, game_dir)
    if installed_from is not None and managed is None:
        raise SetupError("The previous download folder has no installation receipt")
    pins = installer.load_pins(root / "mod/game-pins.json")
    check = installer.preflight(game_dir, pins, "modW3GwentAI", installed_mod=managed)
    print(check.text(), flush=True)
    if not check.ok:
        raise SetupError(
            "The game compatibility check failed; no game files were changed"
        )


def run_step(command: list[str], *, label: str, cwd: Path, env: dict[str, str]) -> None:
    print(label, flush=True)
    runtime = cwd / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    log = runtime / "setup-player.log"
    invalid_output = False
    with log.open("ab") as handle:
        handle.write((label + "\n").encode("utf-8"))
        try:
            with subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            ) as process:
                for raw_line in process.stdout:
                    handle.write(raw_line)
                    handle.flush()
                    try:
                        line = (
                            raw_line.decode("utf-8")
                            .replace("\r\n", "\n")
                            .replace("\r", "\n")
                        )
                    except UnicodeDecodeError:
                        invalid_output = True
                        continue
                    print(line, end="", flush=True)
                returncode = process.wait()
        except OSError as exc:
            raise SetupError(f"{label}: {exc}") from exc
    if invalid_output:
        raise SetupError(
            f"{label}: child output was not UTF-8; original bytes remain in .runtime/setup-player.log; child exit {returncode}"
        )
    if returncode:
        raise SetupError(
            f"{label} failed (exit {returncode}); details remain in .runtime/setup-player.log"
        )


def prepare(root: Path, game_dir: Path, env: dict[str, str]) -> Path:
    def script(name: str) -> str:
        return str(root / "scripts" / name)

    commands = [
        (
            "[1/5] Reading the installed Gwent resources",
            cli_encoding.python_command(
                script("extract_game.py"), "--game-dir", str(game_dir)
            ),
        ),
        (
            "[2/5] Preparing local game classes",
            cli_encoding.python_command(script("decompile_game.py")),
        ),
        (
            "[3/5] Building the local compatibility catalog",
            cli_encoding.python_command(
                script("build_catalog.py"), "--game-dir", str(game_dir)
            ),
        ),
        (
            "[4/5] Compiling and verifying the opponent",
            cli_encoding.python_command(
                script("build_mod.py"),
                "--policy-bundle",
                str(root / "policies"),
                "--output-dir",
                str(root / ".runtime/player-build"),
            ),
        ),
    ]
    for label, command in commands:
        run_step(command, label=label, cwd=root, env=env)
    report = json.loads(
        (root / ".runtime/player-build/manifest.json").read_text(encoding="utf-8")
    )
    if report.get("status") != "offline_compiled_and_export_verified":
        raise SetupError(
            "The local build did not pass verification; no game files were changed"
        )
    print("[5/5] Assembling and checking the local mod package", flush=True)
    installer = _installer()
    try:
        package = installer.assemble_package(
            "ai",
            build_dir=root / ".runtime/player-build",
            package_dir=root / ".runtime/player-package",
        )
        installer._read_package(package)
    except installer.ModInstallError as exc:
        raise SetupError(str(exc)) from exc
    return package


def install(root: Path, game_dir: Path, package: Path) -> None:
    installer = _installer()
    try:
        receipt = installer.install_package(
            game_dir, package_dir=package, runtime_dir=root / ".runtime"
        )
    except installer.ModInstallError as exc:
        raise SetupError(str(exc)) from exc
    print(installer.install_summary(receipt), flush=True)


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    cli_encoding.configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check", action="store_true", help="check download/game only; change nothing"
    )
    modes.add_argument(
        "--prepare-only",
        action="store_true",
        help="prepare a verified local package without installing",
    )
    modes.add_argument(
        "--uninstall",
        action="store_true",
        help="restore the installation recorded in this folder",
    )
    modes.add_argument(
        "--report",
        action="store_true",
        help="show installation and latest-match diagnostics",
    )
    parser.add_argument(
        "--game-dir", type=Path, help="game folder containing bin and content"
    )
    parser.add_argument(
        "--documents", type=Path, help="Documents/The Witcher 3 for --report"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use installed or verified cached tools only",
    )
    parser.add_argument(
        "--installed-from",
        type=Path,
        help="previous download folder; use with --check or --prepare-only",
    )
    args = parser.parse_args(argv)
    if args.installed_from is not None and not (args.check or args.prepare_only):
        parser.error("--installed-from requires --check or --prepare-only")
    try:
        if sys.version_info < (3, 12):
            raise SetupError("Python 3.12 or newer is required")
        if args.uninstall or args.report:
            command = ["--uninstall" if args.uninstall else "--report"]
            if args.game_dir:
                command += ["--game-dir", str(args.game_dir)]
            if args.documents:
                command += ["--documents", str(args.documents)]
            return _installer().main(command)
        game_dir = args.game_dir
        if game_dir is None:
            if not sys.stdin.isatty():
                raise SetupError(
                    "Pass --game-dir with the Witcher 3 folder containing bin and content"
                )
            print("Close The Witcher 3 and its launcher before installation.")
            value = (
                input("Witcher 3 game folder (contains bin and content): ")
                .strip()
                .strip('"')
            )
            if not value:
                raise SetupError("A game folder is required")
            game_dir = Path(value)
        game_dir = game_dir.expanduser().resolve()
        verify_distribution(root)
        check_game(root, game_dir, installed_from=args.installed_from)
        if args.check:
            print("Download and game checks passed. No files were changed.")
            return 0
        if (
            not args.prepare_only
            and (root / ".runtime/mod-install-receipt.json").exists()
        ):
            raise SetupError(
                "This folder already owns an installation. Use --prepare-only to prepare a replacement, then --uninstall before installing it."
            )
        env = ensure_tools(root, offline=args.offline)
        package = prepare(root, game_dir, env)
        if args.prepare_only:
            print(f"Verified local package: {package}; the game was not changed.")
        else:
            install(root, game_dir, package)
            print(
                "Ready. Load a save, open Settings > Mods > Gwent: A Worthy Opponent, and enable AI."
            )
        return 0
    except (SetupError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Setup: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
