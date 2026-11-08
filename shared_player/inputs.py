"""Freeze explicit one-model build inputs without compiling or installing."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def descriptor(path):
    path = Path(path).absolute()
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def source_manifest(root=ROOT):
    root = Path(root)
    paths = []
    for folder, suffixes in {
        "shared_player": {".py"},
        "scripts": {".py", ".java"},
        "mod": {".as", ".js", ".ws", ".xml", ".json"},
        "tests": {".py", ".js"},
    }.items():
        paths.extend(
            p
            for p in (root / folder).rglob("*")
            if p.is_file() and p.suffix in suffixes
        )
    return {
        "format": "shared-player-source-v1",
        "files": {
            p.relative_to(root).as_posix(): descriptor(p)["sha256"]
            for p in sorted(paths)
        },
    }


def prepare(
    *,
    metadata,
    receipt,
    catalog,
    controller,
    fictional_probes,
    actual_probes,
    output,
    edition="release",
    root=ROOT,
    java=None,
    node=None,
    ffdec=None,
):
    root, output = Path(root).absolute(), Path(output).absolute()
    if output.exists():
        raise FileExistsError("fresh build-input directory required")
    sources = source_manifest(root)
    local = root / ".local"
    tools = [
        Path(java or shutil.which("java") or "java"),
        Path(node or shutil.which("node") or "node"),
    ]
    ffdec = Path(ffdec or local / "tools/ffdec/ffdec-cli.jar")
    inputs = [
        local / "vanilla/gameplay/gui_new/swf/gwint/gwint_game.swf",
        local / "vanilla/gameplay/gui_new/swf/gwint/gwint_game.redswf",
        *tools,
        *[p for p in ffdec.parent.rglob("*") if p.is_file()],
    ]
    inventory = {
        "kind": "shared-player-compiler-input-inventory-v1",
        "inputs": [descriptor(p) for p in sorted(set(inputs))],
    }
    specs = {
        name: descriptor(value)
        for name, value in {
            "baseline_metadata": metadata,
            "baseline_receipt": receipt,
            "catalog": catalog,
            "controller": controller,
        }.items()
    }
    probes = [descriptor(fictional_probes)]
    if actual_probes is not None:
        probes.append(descriptor(actual_probes))
    output.mkdir(parents=True)
    for name, data in {
        "source-manifest.json": sources,
        "compiler-inventory.json": inventory,
    }.items():
        (output / name).write_text(json.dumps(data, indent=2) + "\n")
    manifest = {
        "schema": 1,
        "purpose": "shared-player-baseline-compile",
        "artifact_kind": "legacy-baseline",
        "edition": edition,
        **specs,
        "source_manifest": descriptor(output / "source-manifest.json"),
        "compiler_inventory": descriptor(output / "compiler-inventory.json"),
        "numeric_probes": probes,
    }
    target = output / "build.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "metadata",
        "receipt",
        "catalog",
        "controller",
        "fictional-probes",
        "actual-probes",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--edition", choices=("release", "diagnostic"), default="release"
    )
    for name in ("java", "node", "ffdec"):
        parser.add_argument("--" + name, type=Path)
    print(prepare(**vars(parser.parse_args())))


if __name__ == "__main__":
    main()
