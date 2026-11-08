"""Close one-model package proofs before using the existing generic assembler."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .bundle import _json, _write

REFERENCE = Path(__file__).resolve().parents[1]
ASSET_PATHS = {
    "gwai_settings.ws": "mod/ai/settings/scripts/local/gwai_settings.ws",
    "gwai_playtrace.ws": "mod/trace/settings/scripts/local/gwai_playtrace.ws",
    "modW3GwentAI.xml": "mod/ai/settings/menu/modW3GwentAI.xml",
}
DEBUG_SINK = "mod/diagnostic/settings/scripts/local/gwai_policy_diagnostic.ws"


class SharedPackageError(ValueError):
    """The compiled screen and copied package assets do not form one build."""


def _need(condition, message):
    if not condition:
        raise SharedPackageError(message)


def package_assets(variant, *, reference=REFERENCE):
    _need(variant in {"ai", "diagnostic"}, "unsupported shared package variant")
    paths = dict(ASSET_PATHS)
    if variant == "diagnostic":
        paths["gwai_policy_diagnostic.ws"] = DEBUG_SINK
    from .terminal_composition import patch_settings_asset

    assets = {
        name: (reference / relative).read_bytes() for name, relative in paths.items()
    }
    assets["gwai_settings.ws"] = patch_settings_asset(assets["gwai_settings.ws"])
    return assets


def verify_package_ready(report, assets):
    """Require actual compiled observer proof and exact existing asset bytes."""
    _need(
        report.get("format") == "witcher3-gwent-mod-build-v4"
        and report.get("status") == "offline_compiled_and_export_verified",
        "unverified shared compiler artifact",
    )
    variant = report.get("variant")
    _need(variant in {"ai", "diagnostic"}, "unsupported shared package variant")
    expected = set(ASSET_PATHS)
    if variant == "diagnostic":
        expected.add("gwai_policy_diagnostic.ws")
    _need(set(assets) == expected, "release/diagnostic asset inventory differs")
    _need(
        report.get("one_shared_model") is True
        and isinstance(report.get("compiled_helpers"), dict)
        and report["compiled_helpers"].get("model_count") == 1,
        "package requires exactly one compiled shared model",
    )
    trace = report.get("play_trace")
    _need(
        isinstance(trace, dict)
        and trace.get("schema") == "w3gwent-playtrace-v1"
        and trace.get("compiled_verified") is True
        and trace.get("public_only") is True
        and isinstance(trace.get("helper_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", trace["helper_sha256"]) is not None,
        "missing compiled native public trace proof",
    )
    try:
        menu = ET.fromstring(assets["modW3GwentAI.xml"])
    except ET.ParseError as error:
        raise SharedPackageError("invalid existing settings menu") from error
    controls = [item.get("id") for item in menu.findall("./Group/VisibleVars/Var")]
    _need(
        controls
        == [
            "GwaiEnabled",
            "GwaiDevOpponent",
            "GwaiDevStart",
            "GwaiDevReport",
            "GwaiSignal",
            "GwaiTrace",
        ],
        "existing six-control menu inventory differs",
    )
    hashes = {}
    for name, body in assets.items():
        _need(isinstance(body, bytes), "package assets must preserve their bytes")
        hashes[name] = hashlib.sha256(body).hexdigest()
        _need(
            report.get("inputs_sha256", {}).get(name) == hashes[name],
            f"package asset hash identity differs: {name}",
        )
    return {
        "one_shared_model": True,
        "variant": variant,
        "payload_script_count": 2 if variant == "ai" else 3,
        "menu_control_source": "existing-six-control-menu",
        "asset_sha256": hashes,
        "public_trace_compiled": True,
        "native_verified": False,
        "player_latency_verified": False,
        "strength_evidence": False,
    }


def trace_source(build_id, *, diagnostic=False, diagnostic_mode="policy"):
    from scripts import mod_trace

    _need(
        diagnostic_mode in {"policy", "timing-only"}
        and (diagnostic or diagnostic_mode == "policy"),
        "private timing requires diagnostic trace",
    )
    source = mod_trace.helper_source(build_id)
    if diagnostic:
        begin = "      pending = null;\n      if(!enabled) return;"
        finish = "   public static function finish(reason:String, winner:int, manager:CardManager, flow:GwintGameFlowController, round:int) : void\n   {\n"
        emit = "   private static function emit(record:Object) : void\n"
        _need(
            source.count(begin) == source.count(finish) == source.count(emit) == 1,
            "diagnostic lifecycle seam differs",
        )
        source = source.replace(
            begin,
            "      pending = null;\n      GwaiSharedDiagnostic.begin("
            + json.dumps(diagnostic_mode)
            + ","
            + json.dumps(build_id)
            + ");\n      if(!enabled) return;",
            1,
        )
        source = source.replace(
            finish,
            finish
            + (
                "      GwaiSharedDiagnostic.finish(reason,getTimer());\n"
                if diagnostic_mode == "timing-only"
                else "      GwaiSharedDiagnostic.finish(reason);\n"
            ),
            1,
        )
        source = source.replace(
            emit,
            "   public static function encodeDiagnostic(record:Object) : String\n   {\n      return encodeValue(record,0);\n   }\n\n"
            + emit,
            1,
        )
    return source


def patch_public_trace(patched, extra_originals, helper):
    """Reuse existing public native hooks and append exactly one observer class."""
    from scripts import mod_trace

    _need(
        set(extra_originals) == set(mod_trace.EXTRA_CLASSES),
        "trace native class inventory differs",
    )
    declaration = r"\binternal\s+class\s+GwaiPlayTrace\b"
    _need(
        not re.search(declaration, patched["AIPlayerController"]),
        "trace observer class already installed",
    )
    _need(
        len(re.findall(declaration, helper)) == 1,
        "trace observer helper declaration inventory differs",
    )
    result = mod_trace.patch_sources({**patched, **extra_originals})
    ai = result["AIPlayerController"]
    closing = ai.rfind("}")
    _need(
        closing >= 0 and not ai[closing + 1 :].strip(),
        "native package boundary differs",
    )
    result["AIPlayerController"] = ai[:closing] + "\n" + helper + "\n" + ai[closing:]
    return result


def verify_compiled_trace(compiled, helper):
    from scripts import mod_trace

    mod_trace.verify_compiled(compiled, helper)
    return {
        "schema": mod_trace.SCHEMA,
        "compiled_verified": True,
        "public_only": True,
        "helper_sha256": hashlib.sha256(helper.encode("utf-8")).hexdigest(),
    }


def assemble_verified(build_dir, package_dir, *, receipt_path, pins_path=None):
    """Assemble the exact verified derived assets without changing the build."""
    from scripts import install_mod

    build_dir, package_dir, receipt_path = map(
        Path, (build_dir, package_dir, receipt_path)
    )
    _need(
        not package_dir.exists()
        and not package_dir.is_symlink()
        and not receipt_path.exists()
        and not receipt_path.is_symlink(),
        "fresh package and receipt paths required",
    )
    report = _json(build_dir / "manifest.json")
    assets = package_assets(report.get("variant"))
    proof = verify_package_ready(report, assets)
    asset_root = package_dir.parent / (package_dir.name + "-native-assets")
    _need(
        not asset_root.exists() and not asset_root.is_symlink(),
        "fresh native asset root required",
    )
    paths = {**ASSET_PATHS, "gwai_policy_diagnostic.ws": DEBUG_SINK}
    for name, body in assets.items():
        target = asset_root / paths[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    staged_assets = {name: (asset_root / paths[name]).read_bytes() for name in assets}
    verify_package_ready(report, staged_assets)
    package = install_mod.assemble_package(
        report["variant"],
        build_dir=build_dir,
        package_dir=package_dir,
        asset_root=asset_root,
        pins_path=Path(pins_path)
        if pins_path is not None
        else REFERENCE / "mod/game-pins.json",
    )
    manifest, _ = install_mod._read_package(package)
    _need(manifest["variant"] == report["variant"], "assembled package variant differs")
    result = {
        **proof,
        "format": "witcher3-shared-existing-assembler-proof-v1",
        "package_dir": str(package.resolve()),
        "build_manifest_sha256": hashlib.sha256(
            (build_dir / "manifest.json").read_bytes()
        ).hexdigest(),
        "package_manifest_sha256": hashlib.sha256(
            (package / "package-manifest.json").read_bytes()
        ).hexdigest(),
        "native_asset_root": str(asset_root.resolve()),
        "native_asset_files": {
            paths[name]: proof["asset_sha256"][name] for name in assets
        },
        "assembler_source_sha256": hashlib.sha256(
            Path(install_mod.__file__).read_bytes()
        ).hexdigest(),
        "packager_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "assembly_scope": "exact-derived-native-assets-with-existing-compiled-artifact-v1",
        "installed": False,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    _write(receipt_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            assemble_verified(args.build, args.package, receipt_path=args.receipt)
        )
    )


if __name__ == "__main__":
    main()
