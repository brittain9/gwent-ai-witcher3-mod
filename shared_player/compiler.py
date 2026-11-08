"""Offline one-model FFDec integration; installation remains in existing tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .bundle import (
    METADATA_FIELDS,
    _json,
    _write,
    create_bundle,
    materialize,
    validate_metadata,
)
from .compiled_controller import package_function_counts, verify_terminal_bindings
from .gameplay_export import verify_gameplay_binding
from .host import HOSTS, PACKAGE, _body, patch_sources
from .legacy import verify_baseline_binding
from .owned_actions import patch_owned_actions, verify_owned_actions
from .packaging import (
    package_assets,
    patch_public_trace,
    trace_source,
    verify_compiled_trace,
    verify_package_ready,
)
from .primary_counter import patch_primary_counter
from .sources import AS3_ROOT, build_sources, source_identity
from .terminal_composition import patch_terminal_host, terminal_trace
from .timing_hooks import patch_timing_runtime, verify_compiled_timing

REFERENCE = Path(__file__).resolve().parents[1]
PACKAGE_PATH = Path("red/game/witcher3/menus/gwint")


class CompilerError(ValueError):
    """A compiled payload, helper inventory, input or validation differs."""


def patch_native_runtime(originals, helpers, public_trace, *, timing=False):
    """Assemble actual native hosts, public callbacks and primary completion tickets."""
    from scripts import mod_trace

    _need(
        set(originals) == HOSTS | set(mod_trace.EXTRA_CLASSES),
        "complete explicit native runtime source inventory required",
    )
    patched = patch_sources({name: originals[name] for name in HOSTS}, helpers)
    patched = patch_public_trace(
        patched,
        {name: originals[name] for name in mod_trace.EXTRA_CLASSES},
        public_trace,
    )
    patched = patch_owned_actions(patch_primary_counter(patched))
    patched = patch_terminal_host(patched)
    return patch_timing_runtime(patched)[0] if timing else patched


def verify_compiled_primary_counter(native_sources):
    """Require actual compiled callback sites; runtime chain checks are separate."""
    expected = {
        "AIPlayerController": {
            "gwaiPrimaryBegin": 1,
            "gwaiPrimaryPass": 1,
            "gwaiPrimaryObservationReady": 1,
        },
        "BasePlayerController": {
            "gwaiPrimaryTicket": 1,
            "gwaiPrimaryCommitted": 4,
            "gwaiPrimaryComplete": 1,
        },
        "HumanPlayerController": {
            "gwaiPrimaryBegin": 1,
            "gwaiPrimaryPass": 1,
            "gwaiPrimaryComplete": 1,
        },
    }
    for name, methods in expected.items():
        for method, count in methods.items():
            _need(
                len(re.findall(r"\." + method + r"\s*\(", native_sources[name]))
                == count,
                "compiled primary callback inventory differs: " + name + "." + method,
            )
    flow = native_sources["GwintGameFlowController"]
    for method in (
        "gwaiPrimaryReset",
        "gwaiPrimaryBegin",
        "gwaiPrimaryTicket",
        "gwaiPrimaryObservationReady",
        "gwaiPrimaryCommitted",
        "gwaiPrimaryComplete",
        "gwaiPrimaryPass",
        "gwaiPrimaryInvalidate",
        "gwaiPrimaryCounterValid",
    ):
        _body(flow, method)
    start, end = _body(flow, "state_update_PlayerTurn")
    _need(
        "_gwaiDecisionId" not in flow[start:end],
        "compiled poll path still changes the completed-primary counter",
    )
    start, end = _body(flow, "gwaiPrimaryComplete")
    _need(
        re.search(
            r"(?:\+\+\s*(?:this\.)?_gwaiDecisionId|_gwaiDecisionId\s*\+\+)",
            flow[start:end],
        ),
        "compiled accepted-primary completion does not increment the model counter",
    )
    return {
        "counter_contract": "completed-primary-turns-v1",
        "implementation": "native-primary-ticket-v1",
        "compiled_callback_sites": expected,
        "poll_increment_absent": True,
        "runtime_execution_verified": False,
    }


def _need(condition, message):
    if not condition:
        raise CompilerError(message)


def trace_build_identity(
    edition, checkpoint_sha256, source_manifest_sha256, helper_sha256
):
    """Bind loaded trace identity to authored host code and generated helpers."""
    _need(edition in ("release", "diagnostic"), "explicit trace edition required")
    for value in (checkpoint_sha256, source_manifest_sha256, helper_sha256):
        _need(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
            "full trace input SHA256 required",
        )
    identity = {
        "scope": "authored-source-and-generated-core-helpers-v1",
        "edition": edition,
        "checkpoint_sha256": checkpoint_sha256,
        "authored_source_manifest_sha256": source_manifest_sha256,
        "core_helper_source_sha256": helper_sha256,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        **identity,
        "sha256": fingerprint,
        "build_id": f"shared-{edition}-{checkpoint_sha256[:16]}-{fingerprint[:12]}",
    }


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _spec(spec):
    path = Path(spec["path"])
    _need(
        path.is_absolute() and path.is_file(),
        "explicit existing compiler input required",
    )
    _need(_sha(path) == spec["sha256"], f"compiler input changed: {path.name}")
    if "resolved_path" in spec:
        _need(
            str(path.resolve()) == spec["resolved_path"],
            "compiler input resolution changed",
        )
    return path


def verify_compiled_helpers(helpers, metadata):
    """Verify decoded compiled tensors/tables and explicit shared constants."""
    from scripts import compiled_policy

    model_name = f"GwaiNetWeightsData_{metadata['checkpoint_sha256'][:16]}"
    expected = {
        "GwaiCapture",
        "GwaiNetPolicy",
        "GwaiNetWeights",
        "GwaiSharedController",
        "GwaiSharedConfig",
        model_name,
    }
    actual = set(helpers) - {"GwaiPlayTrace"}
    _need(
        actual == expected or actual == expected | {"GwaiSharedDiagnostic"},
        "compiled shared helper inventory differs",
    )
    validate_metadata({field: metadata[field] for field in METADATA_FIELDS})
    try:
        embedded = compiled_policy.verify(helpers, {model_name: metadata})
        _need(
            compiled_policy._constant(helpers[model_name], "DATA_KIND")
            == metadata["data_kind"],
            "compiled payload provenance differs",
        )
        for field in (
            "data_kind",
            "controller_contract",
            "counter_contract",
            "selection_rule",
            "information_protocol",
            "observation_profile_id",
            "controller_source_sha256",
            "training_manifest_sha256",
        ):
            _need(
                compiled_policy._constant(helpers["GwaiSharedConfig"], field.upper())
                == metadata[field],
                f"compiled shared configuration differs: {field}",
            )
    except (ValueError, KeyError, TypeError) as error:
        raise CompilerError(f"compiled policy identity differs: {error}") from error
    policy = helpers["GwaiNetPolicy"]
    _need(
        all(
            re.search(r"\bfunction\s+" + name + r"\s*\(", policy)
            for name in ("evaluate", "valueHead", "trunkOf", "encodeObservation")
        ),
        "compiled policy/value helper is incomplete",
    )
    text = "\n".join(helpers.values())
    _need(
        not re.search(r"\bGwaiNetTierConfig\b|\bGwaiPolicyCore\b|\bGwaiRunner\b", text),
        "compiled shared source contains obsolete controller classes",
    )
    diagnostic = "GwaiSharedDiagnostic" in helpers
    _need(
        ("GwaiSharedDiagnostic.record(" in helpers["GwaiSharedController"])
        == diagnostic,
        "compiled diagnostic sink and call differ",
    )
    _need(
        not re.search(r"\bJSON\s*\.", helpers["GwaiSharedController"]),
        "compiled controller depends on unavailable native JSON",
    )
    if diagnostic:
        observer = helpers["GwaiSharedDiagnostic"]
        _need(
            not re.search(r"\bJSON\s*\.", observer)
            and all(
                name in observer
                for name in (
                    "OnW3GwentPolicyDiagnosticStart",
                    "OnW3GwentPolicyDiagnosticRecord",
                    "OnW3GwentPolicyDiagnosticEnd",
                    "OnW3GwentPolicyDiagnosticError",
                )
            )
            and "GwaiPlayTrace" in helpers
            and "encodeDiagnostic" in helpers["GwaiPlayTrace"]
            and "GwaiSharedDiagnostic.begin" in helpers["GwaiPlayTrace"]
            and "GwaiSharedDiagnostic.finish" in helpers["GwaiPlayTrace"],
            "compiled diagnostic native sink/lifecycle is incomplete",
        )
    return {
        "model_count": 1,
        "embedded_payload_verified": embedded["verified"],
        "models": embedded["models"],
        "shared_contracts_verified": True,
        "vm_execution_verified": False,
    }


def _class_file(directory, name):
    path = Path(directory) / "scripts" / PACKAGE_PATH / f"{name}.as"
    _need(path.is_file(), f"FFDec did not export {name}")
    return path


def _timer_bindings(pcode, expected):
    _need(
        not re.search(r'\bgetlex\s+Multiname\("flash",', pcode),
        "package function resolves an undefined flash root",
    )
    for function, count in expected.items():
        argument_count = 0 if function == "getTimer" else 1
        pattern = rf'\bcallproperty\s+QName\(PackageNamespace\("flash\.utils"\),"{function}"\),\s*{argument_count}\b'
        _need(
            len(re.findall(pattern, pcode)) == count,
            f"compiled binding count differs: {function}",
        )


def verify_inference_binding(metadata, receipt_path):
    receipt = _json(receipt_path)
    _need(
        metadata["data_kind"] == "inference-only"
        and metadata["training_manifest_sha256"] == "unrecorded",
        "inference export must state unknown training provenance",
    )
    _need(
        receipt.get("format") == "shared-inference-export-v1"
        and receipt.get("checkpoint_sha256") == metadata["checkpoint_sha256"]
        and receipt.get("payload_sha256") == metadata["weights_sha256"],
        "inference export identity differs",
    )
    return receipt


def artifact_provenance(manifest, metadata, receipt_path):
    """Use only the explicitly named extant artifact's provenance verifier."""
    choices = {
        "shared-player-inference-compile": ("inference-only", verify_inference_binding),
        "shared-player-baseline-compile": ("legacy-baseline", verify_baseline_binding),
        "shared-player-gameplay-compile": ("shared-gameplay", verify_gameplay_binding),
    }
    _need(manifest.get("purpose") in choices, "unsupported compiler artifact purpose")
    kind, verifier = choices[manifest["purpose"]]
    _need(
        manifest.get("artifact_kind") == kind,
        "compiler artifact kind disagrees with explicit purpose",
    )
    return kind, verifier(metadata, receipt_path)


def verify_running_source(source_manifest_path):
    """Bind actual loaded compiler/helper modules to the registered source tree."""
    import sys

    source_manifest_path = Path(source_manifest_path).resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    expected = root / "shared_player/compiler.py"
    _need(Path(__file__).resolve() == expected, "running compiler source tree differs")
    _need(
        AS3_ROOT.resolve() == root / "mod/ai/as3" and REFERENCE.resolve() == root,
        "running compiler source roots differ",
    )
    entries = _json(source_manifest_path)["files"]
    _need(isinstance(entries, dict), "explicit registered source closure required")
    modules = {"compiler-entrypoint": Path(__file__).resolve()}
    for name, module in tuple(sys.modules.items()):
        if name == "shared_player" or name.startswith("shared_player."):
            location = getattr(module, "__file__", None)
            _need(location is not None, "loaded shared module has no source: " + name)
            modules[name] = Path(location).resolve()
    verified = {}
    for name, location in modules.items():
        _need(
            location.is_relative_to(root / "shared_player"),
            "loaded shared module source tree differs: " + name,
        )
        relative = str(location.relative_to(root))
        _need(
            relative in entries and _sha(location) == entries[relative],
            "loaded shared module source hash differs: " + name,
        )
        verified[name] = entries[relative]
    return {
        "contract": "registered-running-compiler-source-v1",
        "compiler_path": str(expected),
        "source_manifest_sha256": _sha(source_manifest_path),
        "as3_root": str(AS3_ROOT.resolve()),
        "reference_root": str(REFERENCE.resolve()),
        "shared_modules": verified,
    }


def build_player(manifest_path, output_dir):
    """Compile a named historical baseline or genuine learned gameplay export.

    This command never opens a player profile or installs a package. Existing
    oneclick package/profile/native acceptance tools consume its verified build.
    """
    manifest_path, output_dir = Path(manifest_path), Path(output_dir)
    _need(
        not output_dir.exists() and not output_dir.is_symlink(),
        "fresh compiler output required",
    )
    manifest = _json(manifest_path)
    _need(
        manifest.get("schema") == 1
        and manifest.get("purpose")
        in {
            "shared-player-baseline-compile",
            "shared-player-gameplay-compile",
            "shared-player-inference-compile",
        },
        "unsupported compiler manifest",
    )
    running_source = verify_running_source(_spec(manifest["source_manifest"]))
    from scripts import build_probe, cli_encoding, mod_trace

    inventory_path = _spec(manifest["compiler_inventory"])
    inventory = _json(inventory_path)
    for item in inventory["inputs"]:
        _spec(item)
    by_name = {}
    for item in inventory["inputs"]:
        by_name.setdefault(Path(item["path"]).name, []).append(item)

    def original(name):
        _need(len(by_name.get(name, [])) == 1, f"compiler input is ambiguous: {name}")
        return _spec(by_name[name][0])

    swf, redswf, jar, java, node = (
        original(name)
        for name in (
            "gwint_game.swf",
            "gwint_game.redswf",
            "ffdec-cli.jar",
            "java",
            "node",
        )
    )
    for name, digest in build_probe.PINNED_INPUT_SHA256.items():
        _need(_sha(original(name)) == digest, f"reviewed original pin differs: {name}")
    genuine = manifest["purpose"] == "shared-player-gameplay-compile"
    if genuine:
        _need(
            manifest.get("artifact_kind") == "shared-gameplay",
            "explicit genuine artifact kind required",
        )
    metadata_key, receipt_key = (
        ("gameplay_metadata", "gameplay_receipt")
        if genuine
        else ("baseline_metadata", "baseline_receipt")
    )
    raw_metadata_path = _spec(manifest[metadata_key])
    receipt_path = _spec(manifest[receipt_key])
    raw_metadata = _json(raw_metadata_path)
    artifact_kind, provenance = artifact_provenance(
        manifest, raw_metadata, receipt_path
    )
    _need(
        raw_metadata_path.parent == receipt_path.parent / "export",
        "baseline metadata must be the receipt's actual derived export",
    )
    _need(
        _sha(raw_metadata_path.parent / "policy.f32.bin")
        == provenance["payload_sha256"],
        "admitted export payload differs",
    )
    catalog = _spec(manifest["catalog"])
    controller = _json(_spec(manifest["controller"]))
    edition = manifest["edition"]
    _need(edition in {"release", "diagnostic"}, "unsupported compiler edition")
    diagnostic_mode = manifest.get("diagnostic_mode", "policy")
    _need(
        diagnostic_mode in {"policy", "timing-only"}
        and (edition == "diagnostic" or diagnostic_mode == "policy"),
        "private timing requires diagnostic edition",
    )
    probes = [_spec(item) for item in manifest["numeric_probes"]]
    inference_only = manifest["purpose"] == "shared-player-inference-compile"
    _need(
        len(probes) == (1 if inference_only else 2), "numeric dataset inventory differs"
    )
    _need(
        {_json(path, limit=16 * 1024 * 1024)["format"] for path in probes}
        == (
            {"witcher3-policy-probes-v1"}
            if inference_only
            else {
                "witcher3-policy-probes-v1",
                "witcher3-shared-actual-primary-probes-v1",
            }
        ),
        "numeric datasets differ",
    )
    output_dir.mkdir(parents=True)
    logs = output_dir / "logs"
    logs.mkdir()
    started, stage, commands = time.perf_counter(), "prepare", []
    _write(output_dir / "registered-manifest.json", manifest)

    def run(label, command, *, timeout, env=None):
        before = time.perf_counter()
        index = len(commands)
        stdout_path, stderr_path = (
            logs / f"{index:02d}-{label}.stdout.bin",
            logs / f"{index:02d}-{label}.stderr.bin",
        )
        try:
            result = subprocess.run(
                [str(value) for value in command],
                cwd=output_dir,
                env=env,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            stdout_path.write_bytes(error.stdout or b"")
            stderr_path.write_bytes(error.stderr or b"")
            commands.append(
                {
                    "stage": label,
                    "exit": 124,
                    "wall_seconds": time.perf_counter() - before,
                    "stdout_sha256": _sha(stdout_path),
                    "stderr_sha256": _sha(stderr_path),
                }
            )
            raise CompilerError(f"compiler stage timed out: {label}") from error
        stdout_path.write_bytes(result.stdout)
        stderr_path.write_bytes(result.stderr)
        commands.append(
            {
                "stage": label,
                "exit": result.returncode,
                "wall_seconds": time.perf_counter() - before,
                "stdout_sha256": _sha(stdout_path),
                "stderr_sha256": _sha(stderr_path),
            }
        )
        _write(output_dir / "stage-results.json", commands)
        _need(
            result.returncode == 0,
            f"compiler stage failed: {label}; inspect retained logs",
        )
        return cli_encoding.decode_output(result.stdout)

    def ffdec(label, *arguments):
        return run(
            label,
            [
                java,
                *cli_encoding.JAVA_UTF8_OPTIONS,
                "-Xmx1g",
                "-Djava.awt.headless=true",
                "-jar",
                jar,
                *arguments,
            ],
            timeout=180,
        )

    try:
        create_bundle(
            raw_metadata_path.parent, output_dir / "bundle", controller=controller
        )
        prepared = materialize(
            output_dir / "bundle", catalog, output_dir / "materialized"
        )
        helpers = build_sources(
            prepared, edition=edition, diagnostic_mode=diagnostic_mode
        )
        helpers["GwaiCapture"] = (AS3_ROOT / "GwaiCapture.as").read_text(
            encoding="utf-8"
        )
        core_helper_identity = source_identity(helpers)
        trace_identity = trace_build_identity(
            edition,
            prepared.metadata["checkpoint_sha256"],
            manifest["source_manifest"]["sha256"],
            core_helper_identity["sha256"],
        )
        build_id = trace_identity["build_id"]
        public_trace = trace_source(
            build_id,
            diagnostic=edition == "diagnostic",
            diagnostic_mode=diagnostic_mode,
        )
        public_trace = terminal_trace(public_trace)
        assets = package_assets("ai" if edition == "release" else "diagnostic")
        numeric_script = (
            Path(__file__).resolve().parents[1] / "mod/ai/node/shared_numeric.js"
        )

        def numeric(label, source):
            for index, dataset in enumerate(probes):
                result_path = output_dir / f"{label}-{index}.json"
                run(
                    f"{label}-{index}",
                    [
                        node,
                        numeric_script,
                        source,
                        prepared.metadata_path,
                        prepared.weights_path,
                        dataset,
                        result_path,
                    ],
                    timeout=120,
                )
                result = _json(result_path)
                _need(
                    result.get("pass") is True
                    and result.get("servedIndexDisagreements") == 0
                    and result.get("encoderMismatches") == 0
                    and result.get("probabilityMetric") == "critic-loss-draw-win-only",
                    "policy/value numeric gate failed",
                )

        stage = "source-numeric"
        numeric("source-numeric", AS3_ROOT / "GwaiNetPolicy.as")
        stage = "export-original"
        original_export = output_dir / "original-export"
        native_classes = HOSTS | set(mod_trace.EXTRA_CLASSES)
        classes = ",".join(f"{PACKAGE}.{name}" for name in sorted(native_classes))
        ffdec(stage, "-selectclass", classes, "-export", "script", original_export, swf)
        originals = {
            name: _class_file(original_export, name).read_text(encoding="utf-8")
            for name in native_classes
        }
        patched = patch_native_runtime(originals, helpers, public_trace)
        timing_authored = {}
        if diagnostic_mode == "timing-only":
            patched, timing_authored = patch_timing_runtime(patched)
        helpers["GwaiPlayTrace"] = public_trace
        helper_identity = source_identity(helpers)
        _write(output_dir / "helper-identity.json", helper_identity)
        import_root = output_dir / "import"
        package_root = import_root / PACKAGE_PATH
        package_root.mkdir(parents=True)
        for name, source in patched.items():
            (package_root / f"{name}.as").write_text(source, encoding="utf-8")
        stage = "compile"
        compiled_path = output_dir / "gwint_game.swf"
        ffdec(stage, "-importScript", swf, compiled_path, import_root)
        compiled_swf = build_probe.normalize_swf(
            compiled_path.read_bytes(), "shared compiled output"
        )
        stage = "reexport"
        verify_dir = output_dir / "compiled-export"
        ffdec(stage, "-export", "script", verify_dir, compiled_path)
        found = list((verify_dir / "scripts").rglob("Gwai*.as"))
        _need(
            len(found) == len(helpers)
            and {path.stem for path in found} == set(helpers),
            "compiled screen contains an unexpected/missing helper",
        )
        exported_helpers = {
            name: _class_file(verify_dir, name).read_text(encoding="utf-8")
            for name in helpers
        }
        embedded = verify_compiled_helpers(exported_helpers, prepared.metadata)
        compiled_native = {
            name: _class_file(verify_dir, name).read_text(encoding="utf-8")
            for name in native_classes
        }
        public_trace_proof = verify_compiled_trace(
            compiled_native, exported_helpers["GwaiPlayTrace"]
        )
        counter_proof = verify_compiled_primary_counter(compiled_native)
        owned_action_proof = verify_owned_actions(compiled_native)
        timing_proof = (
            verify_compiled_timing(compiled_native, timing_authored)
            if timing_authored
            else None
        )
        stage = "initialization"
        checker = REFERENCE / "scripts/CheckAs3Initialization.java"
        initializer = run(
            stage,
            [
                java,
                *cli_encoding.JAVA_UTF8_OPTIONS,
                "-Djava.awt.headless=true",
                "-cp",
                str(jar.parent / "lib/*"),
                checker,
                compiled_path,
            ],
            timeout=60,
        )
        matched = re.search(r"PASS: (\d+) helper class initializers", initializer)
        _need(
            matched is not None and int(matched.group(1)) == len(helpers),
            "not every actual helper initializer was checked",
        )
        stage = "package-function-bindings"
        expected = package_function_counts(patched, helpers)
        pcode_dir = output_dir / "package-function-bytecode"
        ffdec(
            stage,
            "-selectclass",
            ",".join(f"{PACKAGE}.{name}" for name in expected),
            "-format",
            "script:pcode",
            "-export",
            "script",
            pcode_dir,
            compiled_path,
        )
        for name, counts in expected.items():
            _timer_bindings(
                (pcode_dir / "scripts" / PACKAGE_PATH / f"{name}.pcode").read_text(
                    encoding="utf-8"
                ),
                counts,
            )
        stage = "compiled-numeric"
        numeric("compiled-numeric", _class_file(verify_dir, "GwaiNetPolicy"))
        stage = "compiled-terminal-composition"
        terminal_proof = verify_terminal_bindings(
            compiled_native,
            exported_helpers,
            enabled=controller.get("terminal_pass_v1", False),
        )
        run(
            stage,
            [
                node,
                "--test",
                "--test-name-pattern",
                "terminal pass|terminal-only|optional search baseline",
                Path(__file__).resolve().parents[1]
                / "tests/terminal_composition.test.js",
            ],
            timeout=60,
            env={
                **os.environ,
                "GWAI_SHARED_CONTROLLER_AS3": str(
                    _class_file(verify_dir, "GwaiSharedController")
                ),
            },
        )
        stage = "container-roundtrip"
        original_swf, trailer = build_probe.unpack_redswf(redswf.read_bytes())
        _need(
            original_swf
            == build_probe.normalize_swf(swf.read_bytes(), "reviewed original"),
            "original SWF/REDSWF inputs disagree",
        )
        (output_dir / "gwint_game.redswf").write_bytes(
            build_probe.patch_redswf(redswf.read_bytes(), compiled_swf, trailer)
        )
        artifact_provenance(manifest, _json(raw_metadata_path), receipt_path)
        for item in inventory["inputs"]:
            _spec(item)
        for name in (
            "source_manifest",
            metadata_key,
            receipt_key,
            "catalog",
            "controller",
        ):
            _spec(manifest[name])
        for item in manifest["numeric_probes"]:
            _spec(item)
        report = {
            "format": "witcher3-gwent-mod-build-v4",
            "shared_format": "witcher3-single-policy-player-v1",
            "source_manifest_sha256": manifest["source_manifest"]["sha256"],
            "running_source": running_source,
            "variant": "ai" if edition == "release" else "diagnostic",
            "status": "offline_compiled_and_export_verified",
            "controller_mode": "policy-only",
            "default_enabled": controller["enabled"],
            "one_shared_model": True,
            "helper_identity": helper_identity,
            "diagnostic_mode": diagnostic_mode if edition == "diagnostic" else None,
            "timing_authored_hook_inventory": {
                name: source.count("getTimer(")
                for name, source in timing_authored.items()
            },
            "trace_build_id": build_id,
            "trace_build_identity": trace_identity,
            "core_helper_source_sha256": core_helper_identity["sha256"],
            "compiled_helpers": embedded,
            "class_initialization": {
                "helpers_checked": len(helpers),
                "scope": "static initializer bytecode analysis, not AVM2 execution",
            },
            "artifact_kind": artifact_kind,
            "model_provenance": provenance,
            "play_trace": public_trace_proof,
            "native_primary_counter": counter_proof,
            "owned_action_gate": owned_action_proof,
            "terminal_controller_gate": terminal_proof,
            "package_function_call_inventory": expected,
            "private_timing": timing_proof,
            "raw_export_metadata_sha256": _sha(raw_metadata_path),
            "inputs_sha256": {
                "gwint_game.redswf": _sha(redswf),
                "gwint_game.swf": _sha(swf),
                "ffdec-cli.jar": _sha(jar),
                "CheckAs3Initialization.java": _sha(checker),
                **{
                    name: hashlib.sha256(body).hexdigest()
                    for name, body in assets.items()
                },
            },
            "outputs_sha256": {
                name: _sha(output_dir / name)
                for name in ("gwint_game.swf", "gwint_game.redswf")
            },
            "commands": commands,
            "wall_seconds": time.perf_counter() - started,
            "native_verified": False,
            "installed": False,
            "player_latency_verified": False,
            "difficulty_calibrated": False,
            "lookahead_complete": False,
            "strength_evidence": False,
            "policy_prior_compared": False,
            "rust_numeric_verified": False,
            "package_assembly_ready": False,
        }
        _need(
            package_assets(report["variant"]) == assets,
            "package assets changed during compilation",
        )
        report["package_proof"] = verify_package_ready(report, assets)
        report["package_assembly_ready"] = True
        _write(output_dir / "manifest.json", report)
        return report
    except Exception as error:
        _write(
            output_dir / "failure.json",
            {
                "stage": stage,
                "type": type(error).__name__,
                "message": str(error),
                "commands": commands,
                "wall_seconds": time.perf_counter() - started,
                "installed": False,
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_player(args.manifest, args.output)
    print(
        json.dumps(
            {
                "status": report["status"],
                "wall_seconds": report["wall_seconds"],
                "one_shared_model": True,
                "native_verified": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
