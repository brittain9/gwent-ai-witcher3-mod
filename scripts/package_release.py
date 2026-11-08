#!/usr/bin/env python3
"""Package original mod sources and inference weights for local player setup."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

try:
    from scripts import policy_bundle, policy_probes
except ModuleNotFoundError:
    import policy_bundle
    import policy_probes

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "witcher3-player-release-v1"
VERSION = "0.1.2"
SOURCE_FILES = (
    "shared_player/__init__.py",
    "shared_player/build.py",
    "shared_player/bundle.py",
    "shared_player/compiled_controller.py",
    "shared_player/compiler.py",
    "shared_player/difficulty.py",
    "shared_player/gameplay_export.py",
    "shared_player/host.py",
    "shared_player/host_hooks.py",
    "shared_player/inputs.py",
    "shared_player/legacy.py",
    "shared_player/native_numeric.py",
    "shared_player/owned_actions.py",
    "shared_player/packaging.py",
    "shared_player/primary_counter.py",
    "shared_player/primary_probes.py",
    "shared_player/sources.py",
    "shared_player/terminal_composition.py",
    "shared_player/timing_hooks.py",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE.md",
    "licenses/Better-Gwent-MIT.txt",
    "docs/install.md",
    "docs/building.md",
    "docs/how-it-works.md",
    "docs/evaluation.md",
    "docs/tool-downloads.md",
    "Install.cmd",
    "Uninstall.cmd",
    "Install.sh",
    "Uninstall.sh",
    "scripts/setup_player.py",
    "scripts/cli_encoding.py",
    "scripts/package_release.py",
    "scripts/extract_game.py",
    "scripts/decompile_game.py",
    "scripts/build_catalog.py",
    "scripts/build_mod.py",
    "scripts/build_probe.py",
    "scripts/install_mod.py",
    "scripts/settings_menu.py",
    "scripts/redbundle.py",
    "scripts/mod_practice.py",
    "scripts/mod_trace.py",
    "scripts/mod_diagnostic.py",
    "scripts/harvest_policy_diagnostic.py",
    "scripts/harvest_playtrace.py",
    "scripts/policy_bundle.py",
    "scripts/policy_format.py",
    "scripts/policy_probes.py",
    "scripts/compiled_policy.py",
    "scripts/synthetic_policy_cases.py",
    "scripts/CheckAs3Initialization.java",
    "w3gwent/__init__.py",
    "w3gwent/catalog.py",
    "mod/game-pins.json",
    "mod/ai/as3/GwaiCapture.as",
    "mod/ai/as3/GwaiNetPolicy.as",
    "mod/ai/as3/GwaiNetWeights.as",
    "mod/ai/as3/GwaiPolicyCore.as",
    "mod/ai/as3/GwaiRunner.as",
    "mod/ai/as3/GwaiSharedController.as",
    "mod/ai/as3/GwaiSharedDiagnostic.as",
    "mod/ai/node/shared_numeric.js",
    "mod/ai/node/mod.test.js",
    "mod/ai/node/replay.js",
    "mod/ai/node/diagnostic_replay.js",
    "mod/ai/node/unknown.test.js",
    "mod/ai/node/weights.test.js",
    "mod/ai/settings/menu/modW3GwentAI.xml",
    "mod/ai/settings/scripts/local/gwai_settings.ws",
    "mod/probe-timing/settings/menu/modW3GwentAIProbe.xml",
    "mod/diagnostic/as3/GwaiPolicyDiagnostic.as",
    "mod/diagnostic/settings/scripts/local/gwai_policy_diagnostic.ws",
    "mod/trace/as3/GwaiPlayTrace.as",
    "mod/trace/settings/scripts/local/gwai_playtrace.ws",
)
PRIVATE_MARKERS = (
    "/" + "home/",
    "/" + "Users/",
    "gwent-" + "2018",
    "docs/" + "ai/",
    "docs/" + "sessions/",
    "Co-" + "Authored-By",
)


class ReleaseError(ValueError):
    """A player release input is missing, unverified or outside its source set."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_files(source: Path, source_commit: str | None) -> dict[str, bytes]:
    result = {}
    for name in SOURCE_FILES:
        path = source / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(source)
            or any(
                parent.is_symlink()
                for parent in path.parents
                if parent != source and parent.is_relative_to(source)
            )
        ):
            raise ReleaseError(f"Source symlink cannot ship: {name}")
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise ReleaseError(f"Expected original source text: {name}") from exc
        if any(marker in text for marker in PRIVATE_MARKERS):
            raise ReleaseError(f"Source contains a local reference: {name}")
        if source_commit:
            try:
                frozen = subprocess.check_output(
                    ["git", "show", f"{source_commit}:{name}"],
                    cwd=source,
                    stderr=subprocess.PIPE,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise ReleaseError(f"Source commit does not provide {name}") from exc
            if frozen != data:
                raise ReleaseError(f"Source differs from the named commit: {name}")
        result[name] = data
    return result


def create_release(
    source_root: Path,
    policies_dir: Path,
    output_zip: Path,
    *,
    version: str = VERSION,
    source_commit: str | None = None,
) -> dict[str, object]:
    """Build a new ZIP after verifying every source, model and fictional probe."""
    source_root, policies_dir = source_root.resolve(), policies_dir.resolve()
    output_zip = output_zip.expanduser().resolve()
    checksum_path = output_zip.with_suffix(output_zip.suffix + ".sha256")
    if output_zip.exists() or checksum_path.exists():
        raise ReleaseError(f"Release output already exists: {output_zip.name}")
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ReleaseError("Release version must be MAJOR.MINOR.PATCH")
    if source_commit and not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ReleaseError("Source commit must be its full 40-character Git identity")
    files = _source_files(source_root, source_commit)
    try:
        bundle = policy_bundle.load_bundle(policies_dir)
        if bundle.probes_path is None:
            raise ReleaseError(
                "Policy verification probes are required for a player release"
            )
        policy_probes.validate_bundle_probes(bundle.probes_path, bundle.metadata)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ReleaseError(f"Policy verification failed: {exc}") from exc
    for path in sorted(policies_dir.rglob("*")):
        if path.is_symlink():
            raise ReleaseError("Policy bundle contains a symlink")
        if path.is_file():
            files["policies/" + path.relative_to(policies_dir).as_posix()] = (
                path.read_bytes()
            )
    inventory = {
        name: {"sha256": _hash(data), "bytes": len(data)}
        for name, data in sorted(files.items())
    }
    source_inventory = {name: inventory[name] for name in SOURCE_FILES}
    manifest = {
        "format": FORMAT,
        "version": version,
        "product": "Gwent: Trained Opponents",
        "source_tree_sha256": _hash(
            json.dumps(source_inventory, sort_keys=True, separators=(",", ":")).encode()
        ),
        "policy_manifest_sha256": inventory["policies/manifest.json"]["sha256"],
        "files": inventory,
    }
    if source_commit:
        manifest["source_commit"] = source_commit
    files["release-manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="player-release-", dir=output_zip.parent
    ) as value:
        candidate = Path(value) / "candidate.zip"
        with zipfile.ZipFile(
            candidate, "w", zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for name, data in sorted(files.items()):
                entry = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = (
                    0o100755 if name.endswith(".sh") else 0o100644
                ) << 16
                archive.writestr(entry, data)
        with zipfile.ZipFile(candidate) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(files):
                raise ReleaseError("The generated archive failed verification")
        with output_zip.open("xb") as destination, candidate.open("rb") as source:
            shutil.copyfileobj(source, destination)
    digest = hashlib.sha256()
    with output_zip.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    sha = digest.hexdigest()
    with checksum_path.open("x", encoding="ascii") as handle:
        handle.write(f"{sha}  {output_zip.name}\n")
    return {
        "archive": output_zip.name,
        "sha256": sha,
        "bytes": output_zip.stat().st_size,
        "files": len(files),
        "source_tree_sha256": manifest["source_tree_sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--policies", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--source-commit")
    args = parser.parse_args(argv)
    try:
        result = create_release(
            args.source,
            args.policies,
            args.output,
            version=args.version,
            source_commit=args.source_commit,
        )
    except (ReleaseError, OSError) as exc:
        parser.exit(1, f"package_release: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
