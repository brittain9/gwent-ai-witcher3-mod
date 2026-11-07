#!/usr/bin/env python3
"""Export local Gwent ActionScript and bind the export hashes to its input SWF."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "red.game.witcher3.menus.gwint"
REQUIRED = {
    "CardTemplate",
    "CardManager",
    "CardInstance",
    "CardEffectManager",
    "GwintGameFlowController",
    "AIPlayerController",
    "CardLeaderInstance",
    "BasePlayerController",
}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decompile(
    source: Path, jar: Path, output: Path, *, java: list[str] | None = None
) -> dict:
    source, jar, output = source.resolve(), jar.resolve(), output.resolve()
    swf = source.read_bytes()
    if (
        len(swf) < 10
        or swf[:3] != b"FWS"
        or struct.unpack_from("<I", swf, 4)[0] != len(swf)
    ):
        raise ValueError(
            "input must be the extractor's uncompressed, length-delimited FWS"
        )
    source_hash, tool_hash = file_hash(source), file_hash(jar)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="decompile-", dir=output.parent
    ) as temporary:
        temporary = Path(temporary)
        exported = temporary / "export"
        command = [
            *(java or ["java"]),
            "-Xmx1g",
            "-Djava.awt.headless=true",
            "-jar",
            str(jar),
            "-selectclass",
            f"{PACKAGE}.*",
            "-export",
            "script",
            str(exported),
            str(source),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=temporary,
            timeout=120,
            check=False,
        )
        if completed.returncode:
            raise ValueError(
                f"JPEXS failed: {(completed.stdout + completed.stderr)[-3000:]}"
            )
        package_dir = exported / "scripts" / Path(PACKAGE.replace(".", "/"))
        classes = {path.stem for path in package_dir.glob("*.as")}
        if missing := REQUIRED - classes:
            raise ValueError(
                f"export is missing rule classes: {', '.join(sorted(missing))}"
            )
        if file_hash(source) != source_hash or file_hash(jar) != tool_hash:
            raise ValueError("input SWF or decompiler changed during export")
        files = {
            path.relative_to(exported).as_posix(): file_hash(path)
            for path in sorted(package_dir.glob("*.as"))
        }
        manifest = {
            "schema_version": 1,
            "source_swf_sha256": source_hash,
            "tool_sha256": tool_hash,
            "files": files,
        }
        output.mkdir(parents=True, exist_ok=True)
        for relative in files:
            target = output / relative
            if not target.resolve().is_relative_to(output):
                raise ValueError("export destination escapes output through a symlink")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(exported / relative, target)
        manifest_path = output / "manifest.json"
        if not manifest_path.resolve().is_relative_to(output):
            raise ValueError("manifest destination escapes output through a symlink")
        staging = output / "manifest.json.tmp"
        if staging.is_symlink():
            raise ValueError("temporary manifest must not be a symlink")
        staging.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        staging.replace(manifest_path)
        (output / "decompile.log").write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--swf",
        type=Path,
        default=ROOT / ".local/vanilla/gameplay/gui_new/swf/gwint/gwint_game.swf",
    )
    parser.add_argument(
        "--ffdec", type=Path, default=ROOT / ".local/tools/ffdec/ffdec-cli.jar"
    )
    parser.add_argument("--output", type=Path, default=ROOT / ".local/as3")
    args = parser.parse_args()
    try:
        manifest = decompile(args.swf, args.ffdec, args.output)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"decompile_game: {exc}\n")
    print(f"Exported and attested {len(manifest['files'])} classes to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
