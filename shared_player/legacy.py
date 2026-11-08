"""Explicit frozen-parent provenance conversion; never rewrite a checkpoint.

Torch is loaded only by the separately registered conversion command. Importing
this module and checking historical records require only the standard library.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from .bundle import (
    CONTROLLER_CONTRACT,
    COUNTER_CONTRACT,
    INFORMATION_PROTOCOL,
    METADATA_FIELDS,
    SELECTION_RULE,
    _catalog_tables,
    _object,
    _payload,
    _sha,
    _write,
    validate_metadata,
)


class LegacyEvidenceError(ValueError):
    """Original gameplay evidence or exact tensor identity was not established."""


def _require(condition, message):
    if not condition:
        raise LegacyEvidenceError(message)


def admit_legacy_records(inventory, run, index, metadata, relocation):
    """Admit one selected historical snapshot without changing training claims."""
    _require(
        inventory.get("data_kind") == "gameplay", "explicit gameplay evidence required"
    )
    inputs, historical = inventory["inputs"], inventory["historical_training"]
    original = _sha(inputs["original_checkpoint"]["sha256"], "original checkpoint")
    relocated = _sha(inputs["relocated_checkpoint"]["sha256"], "relocated checkpoint")
    source = _sha(historical["code_tree_sha256"], "historical training source")
    training = _sha(inputs["training_run"]["sha256"], "authentic training record")
    _require(
        run.get("run_id") == historical.get("run_id") and bool(run.get("run_id")),
        "historical run identity differs",
    )
    _require(
        run.get("code_tree_sha256") == source, "historical source identity differs"
    )
    source_files = historical.get("source_file_sha256")
    _require(
        isinstance(source_files, dict)
        and bool(source_files)
        and run.get("source_file_sha256") == source_files,
        "historical source file identities differ",
    )
    _require(
        run.get("fast_observations") is True, "historical observation audit differs"
    )
    _require(
        type(run.get("games_completed")) is int and run["games_completed"] > 0,
        "historical gameplay count is missing",
    )
    for field in ("controller", "counter", "observation_profile"):
        _require(
            isinstance(historical.get(field), str) and bool(historical[field]),
            f"historical {field} must remain explicit",
        )
    selected = [row for row in index if row.get("sha256") == original]
    _require(
        len(selected) == 1, "selected snapshot needs exactly one authentic index row"
    )
    selected = selected[0]
    _require(
        type(selected.get("updates")) is int
        and selected["updates"] >= 0
        and type(selected.get("games_completed")) is int
        and selected["games_completed"] > 0,
        "selected snapshot has no training update/game count",
    )
    offset = run.get("update_offset")
    _require(type(offset) is int and offset >= 0, "historical update offset is missing")
    _require(
        metadata.get("checkpoint_sha256") == relocated
        and metadata.get("update_count") == offset + selected["updates"],
        "selected snapshot/export update identity differs",
    )
    matching = [
        row
        for row in relocation
        if row.get("previous_sha256") == original and row.get("sha256") == relocated
    ]
    _require(
        len(matching) == 1, "explicit original-to-relocated identity record required"
    )
    return {
        "data_kind": "gameplay",
        "training_manifest_sha256": training,
        "controller_source_sha256": source,
        "controller_source_scope": "historical-training-code-tree-v1",
        "selected_snapshot_sha256": original,
        "relocated_checkpoint_sha256": relocated,
        "selected_snapshot_updates": selected["updates"],
        "global_updates": offset + selected["updates"],
        "historical_training": copy.deepcopy(historical),
        "trained_with_serving_controller": False,
    }


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_object)


def derive_serving_metadata(metadata, evidence):
    """Carry historical training scope with derived serving metadata itself."""
    return {
        **copy.deepcopy(metadata),
        "data_kind": "gameplay",
        "observation_profile_id": "faction-unknown-v1",
        "controller_contract": CONTROLLER_CONTRACT,
        "counter_contract": COUNTER_CONTRACT,
        "selection_rule": SELECTION_RULE,
        "information_protocol": INFORMATION_PROTOCOL,
        "controller_source_sha256": evidence["controller_source_sha256"],
        "controller_source_scope": evidence["controller_source_scope"],
        "training_manifest_sha256": evidence["training_manifest_sha256"],
        "serving_contract_scope": "derived-serving-only-not-historical-training",
        "trained_with_serving_controller": False,
        "historical_training": copy.deepcopy(evidence["historical_training"]),
        "selected_snapshot_sha256": evidence["selected_snapshot_sha256"],
        "relocated_checkpoint_sha256": evidence["relocated_checkpoint_sha256"],
    }


def verify_baseline_binding(metadata, receipt_path):
    """Require raw exporter provenance and its exact receipt in every build."""
    path = Path(receipt_path)
    _require(path.is_file() and not path.is_symlink(), "missing conversion receipt")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _require(
        metadata.get("baseline_conversion_record") == "../conversion.json"
        and metadata.get("baseline_conversion_sha256") == digest,
        "conversion receipt binding differs",
    )
    receipt = _read(path)
    _require(
        receipt.get("format") == "witcher3-explicit-legacy-serving-conversion-v1",
        "unsupported conversion receipt",
    )
    for field in (
        "data_kind",
        "training_manifest_sha256",
        "controller_source_sha256",
        "controller_source_scope",
        "serving_contract_scope",
        "trained_with_serving_controller",
        "historical_training",
        "selected_snapshot_sha256",
        "relocated_checkpoint_sha256",
    ):
        _require(
            field in metadata and metadata[field] == receipt.get(field),
            f"derived metadata lost conversion scope: {field}",
        )
    _require(
        metadata["controller_source_scope"] == "historical-training-code-tree-v1"
        and metadata["serving_contract_scope"]
        == "derived-serving-only-not-historical-training"
        and metadata["trained_with_serving_controller"] is False
        and receipt["trained_with_serving_controller"] is False
        and metadata["data_kind"] == "gameplay",
        "conversion must preserve historical training scope",
    )
    _require(
        receipt.get("tensor_identity_verified") is True
        and receipt.get("fresh_tensor_payload_equals_original_export") is True
        and receipt.get("checkpoint_rewritten") is False
        and receipt.get("new_training_performed") is False,
        "conversion has no valid original tensor identity",
    )
    _require(
        metadata.get("checkpoint_sha256") == receipt["relocated_checkpoint_sha256"],
        "derived model checkpoint differs from conversion",
    )
    return {**copy.deepcopy(receipt), "conversion_sha256": digest}


def preflight_legacy(inventory_path):
    """Bind explicit files and historical evidence before importing Torch."""
    inventory_path = Path(inventory_path)
    inventory = _read(inventory_path)
    _require(
        inventory.get("purpose") == "compiler-identity-baseline-inputs-only",
        "explicit baseline conversion inventory required",
    )
    inputs = inventory["inputs"]
    required = {
        "original_checkpoint",
        "relocated_checkpoint",
        "export_metadata",
        "export_weights",
        "training_run",
        "snapshot_index",
        "relocation_record",
        "input_audit",
        "source_baseline",
    }
    _require(set(inputs) == required, "baseline input inventory differs")
    paths = {}
    for name, spec in inputs.items():
        path = Path(spec["path"])
        _require(
            path.is_absolute() and path.is_file() and not path.is_symlink(),
            f"missing or linked baseline input {name}",
        )
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        _require(
            digest == spec["sha256"] and path.stat().st_size == spec["bytes"],
            f"baseline input changed: {name}",
        )
        paths[name] = path
    run, metadata = _read(paths["training_run"]), _read(paths["export_metadata"])
    index = [
        json.loads(line, object_pairs_hook=_object)
        for line in paths["snapshot_index"].read_text().splitlines()
        if line
    ]
    evidence = admit_legacy_records(
        inventory, run, index, metadata, _read(paths["relocation_record"])
    )
    expected_serving = {
        "controller_contract": CONTROLLER_CONTRACT,
        "counter_contract": COUNTER_CONTRACT,
        "observation_profile_id": "faction-unknown-v1",
        "selection_rule": SELECTION_RULE,
        "shared_enabled_default": False,
    }
    _require(
        inventory.get("proposed_serving") == expected_serving,
        "reviewed baseline serving scope differs",
    )
    return inventory, paths, metadata, evidence


def convert_baseline(inventory_path, catalog_path, output_dir):
    """Verify real tensors and write a separately identified derived export."""
    output_dir = Path(output_dir)
    _require(
        not output_dir.exists() and not output_dir.is_symlink(),
        "baseline output must be fresh",
    )
    started = time.perf_counter()
    inventory, paths, metadata, evidence = preflight_legacy(inventory_path)
    preflight_seconds = time.perf_counter() - started
    import_started = time.perf_counter()
    import torch

    from learning.model import AtaraxosPolicyValue

    import_seconds = time.perf_counter() - import_started
    torch.set_num_threads(1)
    load_started = time.perf_counter()
    original = torch.load(
        paths["original_checkpoint"], map_location="cpu", weights_only=False
    )
    relocated = torch.load(
        paths["relocated_checkpoint"], map_location="cpu", weights_only=False
    )
    load_seconds = time.perf_counter() - load_started
    verify_started = time.perf_counter()
    states = [checkpoint["policy_state"] for checkpoint in (original, relocated)]
    _require(
        set(states[0]) == set(states[1]) == set(metadata["offsets"]),
        "checkpoint tensor inventory differs",
    )
    for checkpoint in (original, relocated):
        config = checkpoint["model_config"]
        for field in ("observation_size", "card_count", "width", "blocks"):
            _require(
                config.get(field) == metadata[field],
                f"checkpoint model config differs: {field}",
            )
        identity = checkpoint["identity"]
        _require(
            identity.get("run_id") == inventory["historical_training"]["run_id"]
            and identity.get("updates") == metadata["update_count"],
            "checkpoint training identity differs",
        )
        model = AtaraxosPolicyValue(
            config["observation_size"],
            config["card_count"],
            width=config["width"],
            blocks=config["blocks"],
        )
        model.load_state_dict(checkpoint["policy_state"], strict=True)
        del model
    payload_parts, cursor = [], 0
    for name in sorted(metadata["offsets"], key=metadata["offsets"].get):
        left, right = states[0][name], states[1][name]
        _require(
            left.dtype == right.dtype == torch.float32
            and list(left.shape) == list(right.shape) == metadata["shapes"][name],
            f"checkpoint tensor dtype/shape differs: {name}",
        )
        _require(
            torch.equal(left, right) and bool(torch.isfinite(left).all()),
            f"checkpoint policy tensor differs or is nonfinite: {name}",
        )
        _require(
            metadata["offsets"][name] == cursor,
            "checkpoint tensor offsets are not contiguous",
        )
        flat = left.detach().cpu().contiguous().view(-1)
        payload_parts.append(flat.numpy().astype("<f4", copy=False).tobytes())
        cursor += flat.numel()
    payload = b"".join(payload_parts)
    _require(
        payload == paths["export_weights"].read_bytes(),
        "fresh original checkpoint tensors differ from frozen export payload",
    )
    catalog = _read(catalog_path)
    ids, effects = _catalog_tables(catalog, metadata)
    _require(
        relocated["identity"].get("card_ids") == ids
        and relocated["identity"].get("catalog_sha256") == metadata["catalog_sha256"],
        "relocated checkpoint catalog identity differs",
    )
    derived = {
        **derive_serving_metadata(metadata, evidence),
        "card_ids": ids,
        "card_effects": effects,
    }
    validate_metadata({name: derived[name] for name in METADATA_FIELDS})
    _payload(paths["export_weights"], derived)
    verification_seconds = time.perf_counter() - verify_started
    # Recheck every input after loading/verification, before any successful output.
    preflight_legacy(inventory_path)
    receipt = {
        "format": "witcher3-explicit-legacy-serving-conversion-v1",
        **evidence,
        "inventory_sha256": hashlib.sha256(
            Path(inventory_path).read_bytes()
        ).hexdigest(),
        "catalog_file_sha256": hashlib.sha256(
            Path(catalog_path).read_bytes()
        ).hexdigest(),
        "input_identities": inventory["inputs"],
        "serving_contract_scope": "derived-serving-only-not-historical-training",
        "serving": inventory["proposed_serving"],
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "tensor_identity_verified": True,
        "fresh_tensor_payload_equals_original_export": True,
        "checkpoint_rewritten": False,
        "new_training_performed": False,
        "native_verified": False,
        "strength_evidence": False,
        "timings_seconds": {
            "preflight": preflight_seconds,
            "torch_import": import_seconds,
            "checkpoint_load": load_seconds,
            "tensor_verification": verification_seconds,
        },
    }
    receipt_bytes = (
        json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    derived["baseline_conversion_record"] = "../conversion.json"
    derived["baseline_conversion_sha256"] = hashlib.sha256(receipt_bytes).hexdigest()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}-", dir=output_dir.parent
    ) as temporary:
        staging = Path(temporary) / "conversion"
        (staging / "export").mkdir(parents=True)
        (staging / "export/policy.f32.bin").write_bytes(payload)
        _write(staging / "export/metadata.json", derived)
        _write(staging / "conversion.json", receipt)
        verify_baseline_binding(derived, staging / "conversion.json")
        os.rename(staging, output_dir)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = convert_baseline(args.inventory, args.catalog, args.output)
    print(json.dumps(receipt, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
