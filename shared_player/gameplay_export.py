"""Explicit genuine gameplay export; Torch is lazy and legacy evidence is separate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import time
from pathlib import Path

from .bundle import METADATA_FIELDS, _payload, _write, validate_metadata

CONTRACTS = {
    "observation_schema_id": "witcher3-gwent-seat-view-v1",
    "action_schema_id": "witcher3-gwent-factorized-action-v1",
    "controller_contract": "deployed-controller-v1",
    "counter_contract": "completed-primary-turns-v1",
    "selection_rule": "served-group-first-v1",
}
PROFILES = {"full-faction-v1", "faction-unknown-v1"}
RECEIPT_FIELDS = {"shared_export_record", "shared_export_sha256"}


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    _need(
        isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value),
        "explicit SHA256 required",
    )
    return value


def _positive(value):
    _need(type(value) is int and value > 0, "positive actual count required")
    return value


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _need(key not in result, "duplicate provenance JSON field")
            result[key] = value
        return result

    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def _file(spec):
    _need(isinstance(spec, dict), "explicit file identity required")
    path = Path(spec["path"])
    _need(
        path.is_absolute()
        and path.is_file()
        and _sha(path) == _hash(spec.get("sha256")),
        "registered provenance file changed",
    )
    if "resolved_path" in spec:
        _need(
            str(path.resolve()) == spec["resolved_path"],
            "registered file resolution differs",
        )
    return path


def admit_gameplay_identity(
    identity,
    config,
    *,
    training_manifest_sha256,
    controller_source_sha256,
    completed_updates,
):
    """Admit explicit training identity, never manufacture gameplay from a default."""
    _need(
        identity.get("data_kind") == "gameplay"
        and identity.get("target_source") == "onpolicy",
        "genuine on-policy gameplay identity required",
    )
    _need(
        identity.get("behavior_selection_rule") == "factorized-categorical-v1",
        "actual stochastic behavior law required",
    )
    _need(
        identity.get("information_protocol") == "seat-view-v1",
        "fair training information protocol required",
    )
    _need(
        identity.get("controller_source_scope")
        == "native-controller-source-manifest-v1",
        "native training source scope required",
    )
    _need(
        identity.get("updates_scope") == "completed-optimizer-steps-in-this-run-v1",
        "actual optimizer count scope required",
    )
    _need(
        identity.get("training_manifest_sha256") == _hash(training_manifest_sha256),
        "training manifest identity differs",
    )
    _need(
        identity.get("controller_source_sha256") == _hash(controller_source_sha256),
        "training controller identity differs",
    )
    _need(
        _positive(identity.get("updates")) == _positive(completed_updates),
        "completed optimizer steps differ from producer result",
    )
    generation = _positive(identity.get("update_id"))
    for key, expected in CONTRACTS.items():
        _need(config.get(key) == expected, "checkpoint contract differs: " + key)
    _need(
        config.get("observation_profile_id") in PROFILES,
        "explicit model profile required",
    )
    for key in ("tensor_sha256", "catalog_sha256"):
        _hash(identity.get(key))
    cards = identity.get("card_ids")
    _need(
        isinstance(cards, list)
        and bool(cards)
        and all(type(card) is int and card >= 0 for card in cards)
        and cards == sorted(set(cards))
        and config.get("card_count") == len(cards),
        "canonical model vocabulary required",
    )
    for key in ("width", "observation_size"):
        _positive(config.get(key))
    _need(
        type(config.get("blocks")) is int and config["blocks"] >= 0,
        "explicit network depth required",
    )
    _need(
        isinstance(identity.get("run_id"), str) and bool(identity["run_id"]),
        "actual model run identity required",
    )
    initialization = identity.get("initialization")
    _need(
        isinstance(initialization, dict)
        and initialization.get("kind") in {"parent", "random"},
        "actual initialization required",
    )
    if initialization["kind"] == "parent":
        _hash(initialization.get("checkpoint_sha256"))
        _hash(initialization.get("tensor_sha256"))
    else:
        _need(
            type(initialization.get("seed")) is int and initialization["seed"] >= 0,
            "registered random initialization seed required",
        )
    return {
        **CONTRACTS,
        "data_kind": "gameplay",
        "target_source": "onpolicy",
        "behavior_selection_rule": identity["behavior_selection_rule"],
        "information_protocol": identity["information_protocol"],
        "observation_profile_id": config["observation_profile_id"],
        "training_manifest_sha256": training_manifest_sha256,
        "controller_source_sha256": controller_source_sha256,
        "controller_source_scope": identity["controller_source_scope"],
        "update_count": identity["updates"],
        "updates_scope": identity["updates_scope"],
        "behavior_generation": generation,
        "tensor_sha256": identity["tensor_sha256"],
        "initialization": copy.deepcopy(initialization),
        "training_implementation_scope": "native-primary-onpolicy-v1",
        "serving_implementation_scope": "actionscript-policy-only-export-v1",
        "trained_with_actionscript_simulator": False,
    }


def _runtime_identity(source_manifest, expected):
    root = source_manifest.parent
    entries = _read(source_manifest)["files"]
    _need(isinstance(entries, list), "training source manifest inventory required")
    selected = []
    seen = set()
    for item in entries:
        relative = Path(item["relative"])
        _need(
            not relative.is_absolute()
            and ".." not in relative.parts
            and str(relative) not in seen,
            "invalid/duplicate training source path",
        )
        seen.add(str(relative))
        if str(relative) in {"pyproject.toml", "uv.lock"} or (
            relative.suffix == ".py"
            and relative.parts[0]
            in {"learning", "w3gwent", "scripts", "shared_learning"}
        ):
            _need(
                _sha(root / relative) == item["sha256"],
                "training runtime source changed",
            )
            selected.append((str(relative), item["sha256"]))
    _need(
        {
            "pyproject.toml",
            "uv.lock",
            "shared_learning/onpolicy_run.py",
            "shared_learning/onpolicy_training.py",
        }
        <= {item[0] for item in selected},
        "training source inventory incomplete",
    )
    actual_files = {
        str(path.relative_to(root))
        for folder in ("learning", "w3gwent", "scripts", "shared_learning")
        for path in (root / folder).rglob("*.py")
    }
    actual_files.update({"pyproject.toml", "uv.lock"})
    _need(
        actual_files == {item[0] for item in selected},
        "training source manifest omitted runtime files",
    )
    digest = hashlib.sha256()
    for relative, file_sha in sorted(selected):
        digest.update(relative.encode() + b"\0" + file_sha.encode() + b"\n")
    _need(digest.hexdigest() == _hash(expected), "training runtime identity differs")


def preflight_gameplay(plan_path):
    """Bind actual successful producer/corpus/files before loading any checkpoint."""
    plan = _read(plan_path)
    _need(
        plan.get("format") == "witcher3-genuine-shared-export-inputs-v1",
        "explicit genuine export plan required",
    )
    required = {
        "checkpoint",
        "training_manifest",
        "pipeline_result",
        "training_source_manifest",
    }
    _need(set(plan.get("inputs", {})) == required, "genuine input inventory differs")
    paths = {name: _file(item) for name, item in plan["inputs"].items()}
    manifest = _read(paths["training_manifest"])
    result = _read(paths["pipeline_result"])
    manifest_sha = plan["inputs"]["training_manifest"]["sha256"]
    _need(
        manifest.get("purpose")
        in {"native-primary-onpolicy-pilot", "native-primary-onpolicy-training"},
        "actual supported gameplay producer required",
    )
    _need(
        result.get("status") == "pass"
        and result.get("data_kind") == "gameplay"
        and result.get("purpose") == manifest["purpose"]
        and result.get("run_id") == manifest["run_id"]
        and result.get("training_manifest_sha256") == manifest_sha,
        "pipeline did not pass with this exact manifest",
    )
    _need(
        result.get("reload_tensor_identity_verified") is True
        and result.get("frozen_parent_identity_verified") is True,
        "producer tensor/reload checks did not pass",
    )
    for field in ("games_failed", "games_incomplete", "games_unstarted"):
        _need(
            type(result.get(field)) is int and result[field] == 0,
            "failed/incomplete producer cannot enter player export",
        )
    _need(
        _positive(result.get("games_completed"))
        == _positive(result.get("games_planned")),
        "producer lost planned games",
    )
    for stage in ("train", "validation"):
        counts = result["stage_counts"][stage]
        _need(
            _positive(counts.get("completed")) == _positive(counts.get("planned"))
            and counts.get("failed") == 0
            and counts.get("unstarted") == 0,
            "producer stage did not complete",
        )
    for name in (
        "engine",
        "catalog",
        "controller_source",
        "initial_checkpoint",
        "train_cohort",
        "validation_cohort",
    ):
        paths[name] = _file(manifest[name])
    _need(
        manifest["engine"].get("controller_source_sha256")
        == manifest["controller_source"]["sha256"],
        "training engine/controller source binding differs",
    )
    _runtime_identity(
        paths["training_source_manifest"], manifest["runtime_source_sha256"]
    )
    checkpoint = result.get("checkpoint", {})
    _need(
        checkpoint.get("sha256") == plan["inputs"]["checkpoint"]["sha256"]
        and Path(checkpoint.get("path", "/")).resolve()
        == paths["checkpoint"].resolve(),
        "producer checkpoint artifact differs",
    )
    _hash(checkpoint.get("tensor_sha256"))
    steps = _positive(result.get("completed_optimizer_updates"))
    optimizer_results = result.get("optimizer_results")
    _need(
        isinstance(optimizer_results, list) and bool(optimizer_results),
        "actual optimizer results required",
    )
    _need(
        all(item.get("finite_update") is True for item in optimizer_results)
        and sum(_positive(item.get("optimizer_steps")) for item in optimizer_results)
        == steps,
        "finite actual optimizer accounting differs",
    )
    _need(
        optimizer_results[-1].get("tensor_sha256_after") == checkpoint["tensor_sha256"],
        "final learned tensors differ from producer",
    )
    corpora_specs = plan.get("corpus_receipts")
    _need(
        isinstance(corpora_specs, list) and bool(corpora_specs),
        "actual corpus receipt files required",
    )
    corpora_paths = [_file(item) for item in corpora_specs]
    _need(len(set(corpora_paths)) == len(corpora_paths), "duplicate corpus receipt")
    corpora = [_read(path) for path in corpora_paths]
    _need(
        len(optimizer_results) == len(corpora),
        "optimizer results must match actual complete batches",
    )
    episodes = []
    for index, corpus in enumerate(corpora):
        _need(
            corpus.get("training_manifest_sha256") == manifest_sha
            and corpus.get("batch_index") == index
            and corpus.get("failed_training_games") == 0
            and corpus.get("update_id") == index,
            "corpus batch identity/count differs",
        )
        _need(
            _positive(corpus.get("complete_training_games"))
            == len(corpus.get("episodes", [])),
            "corpus lost complete episodes",
        )
        _positive(corpus.get("rows"))
        _hash(corpus.get("policy_revision"))
        if index:
            _need(
                corpus["policy_revision"]
                == optimizer_results[index - 1].get("tensor_sha256_after"),
                "behavior revision does not follow actual previous batch tensors",
            )
        episodes.extend(corpus["episodes"])
    _need(
        len(episodes) == result["stage_counts"]["train"]["completed"]
        and sum(corpus["rows"] for corpus in corpora) == result["training_rows"]
        and len(corpora) == result["completed_training_batches"],
        "producer/corpus accounting differs",
    )
    _need(
        len({item["episode_id"] for item in episodes}) == len(episodes)
        and len({item["journal_path"] for item in episodes}) == len(episodes),
        "duplicate training episode/journal",
    )
    journal_specs = []
    for episode in episodes:
        item = {"path": episode["journal_path"], "sha256": episode["journal_sha256"]}
        path = _file(item)
        _need(
            path.stat().st_size == _positive(episode.get("journal_bytes")),
            "corpus journal byte count differs",
        )
        journal_specs.append(item)
    expected_corpus = (
        corpora[0]
        if len(corpora) == 1 and manifest["purpose"] == "native-primary-onpolicy-pilot"
        else {
            "training_manifest_sha256": manifest_sha,
            "complete_training_games": len(episodes),
            "failed_training_games": 0,
            "rows": result["training_rows"],
            "batches": corpora,
            "fixed_final_batch_count": len(corpora),
            "intermediate_validation_reads": 0,
        }
    )
    return {
        "plan": plan,
        "paths": paths,
        "manifest": manifest,
        "result": result,
        "corpora": corpora,
        "expected_checkpoint_corpus": expected_corpus,
        "journal_specs": journal_specs,
        "completed_updates": steps,
    }


def _tensor_identity(state):
    import torch

    digest = hashlib.sha256()
    for name, tensor in sorted(state.items()):
        value = tensor.detach().cpu().contiguous()
        _need(
            value.dtype == torch.float32 and bool(torch.isfinite(value).all()),
            "player export needs finite float32 tensors",
        )
        digest.update(
            json.dumps(
                [name, str(value.dtype), list(value.shape)], separators=(",", ":")
            ).encode()
            + b"\0"
        )
        digest.update(value.reshape(-1).view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def verify_gameplay_binding(metadata, receipt_path):
    """Bind a genuine export's metadata to its distinct immutable receipt."""
    path = Path(receipt_path)
    _need(path.is_file() and not path.is_symlink(), "missing genuine export receipt")
    _need(
        metadata.get("shared_export_record") == "../shared-export.json"
        and metadata.get("shared_export_sha256") == _sha(path),
        "genuine export receipt binding differs",
    )
    receipt = _read(path)
    _need(
        metadata.get("data_kind") == "gameplay"
        and metadata.get("target_source") == "onpolicy",
        "genuine metadata data kind/source differs",
    )
    for key, value in CONTRACTS.items():
        _need(metadata.get(key) == value, "genuine serving contract differs: " + key)
    _need(
        metadata.get("observation_profile_id") in PROFILES,
        "genuine model profile missing",
    )
    _need(
        receipt.get("format") == "witcher3-genuine-shared-export-v1"
        and receipt.get("data_kind") == "gameplay"
        and receipt.get("checkpoint_rewritten") is False
        and receipt.get("tensor_identity_verified") is True,
        "unsupported/incomplete genuine export receipt",
    )
    unsigned = {
        key: value for key, value in metadata.items() if key not in RECEIPT_FIELDS
    }
    _need(
        hashlib.sha256(_canonical(unsigned)).hexdigest()
        == receipt.get("metadata_identity_sha256"),
        "genuine metadata identity changed",
    )
    _need(
        metadata.get("checkpoint_sha256") == receipt.get("checkpoint_sha256")
        and metadata.get("tensor_sha256") == receipt.get("tensor_sha256")
        and metadata.get("export_sha256") == receipt.get("payload_sha256")
        and metadata.get("controller_source_scope")
        == "native-controller-source-manifest-v1",
        "genuine checkpoint/tensor/payload/scope differs",
    )
    _need(
        "baseline_conversion_record" not in metadata
        and "historical_training" not in metadata,
        "genuine export cannot impersonate a legacy conversion",
    )
    return {**receipt, "receipt_sha256": _sha(path)}


def export_gameplay(plan_path, output_dir):
    """Export one verified actual checkpoint without modifying its bytes."""
    output = Path(output_dir)
    _need(
        not output.exists() and not output.is_symlink(),
        "fresh gameplay export output required",
    )
    started = time.perf_counter()
    preflight = preflight_gameplay(plan_path)
    preflight_seconds = time.perf_counter() - started
    import_started = time.perf_counter()
    import torch

    from scripts.export_policy_as3 import export

    import_seconds = time.perf_counter() - import_started
    torch.set_num_threads(1)
    checkpoint = torch.load(
        preflight["paths"]["checkpoint"], map_location="cpu", weights_only=True
    )
    identity, config = checkpoint["identity"], checkpoint["model_config"]
    evidence = admit_gameplay_identity(
        identity,
        config,
        training_manifest_sha256=preflight["plan"]["inputs"]["training_manifest"][
            "sha256"
        ],
        controller_source_sha256=preflight["manifest"]["controller_source"]["sha256"],
        completed_updates=preflight["completed_updates"],
    )
    _need(
        checkpoint.get("gameplay_receipt") == preflight["expected_checkpoint_corpus"],
        "checkpoint lost actual aggregate corpus provenance",
    )
    _need(
        identity["update_id"] == len(preflight["corpora"]),
        "behavior generation differs from complete batch count",
    )
    _need(
        identity["initialization"]["kind"] == "parent"
        and identity["initialization"]["checkpoint_sha256"]
        == preflight["manifest"]["initial_checkpoint"]["sha256"],
        "current actual producer initialization differs",
    )
    _need(
        config["observation_profile_id"]
        == preflight["manifest"]["observation_profile_id"],
        "training model profile differs",
    )
    actual_tensor = _tensor_identity(checkpoint["policy_state"])
    _need(
        actual_tensor
        == identity["tensor_sha256"]
        == preflight["result"]["checkpoint"]["tensor_sha256"],
        "actual learned tensors changed",
    )
    parent = torch.load(
        preflight["paths"]["initial_checkpoint"], map_location="cpu", weights_only=False
    )
    _need(
        _tensor_identity(parent["policy_state"])
        == identity["initialization"]["tensor_sha256"],
        "actual initialization parent tensors differ",
    )
    _need(
        preflight["corpora"][0]["policy_revision"]
        == identity["initialization"]["tensor_sha256"],
        "first behavior revision differs from actual parent tensors",
    )
    del parent
    # Recheck all bytes before writing a fresh derived export.
    preflight_gameplay(plan_path)
    output.mkdir(parents=True)
    stage = "generic-numerical-export"
    try:
        metadata = export(
            preflight["paths"]["checkpoint"],
            preflight["paths"]["catalog"],
            output / "export",
            class_name="GwaiNetWeightsData_"
            + preflight["plan"]["inputs"]["checkpoint"]["sha256"][:16],
            observation_profile_id=config["observation_profile_id"],
        )
        _need(
            metadata["update_count"] == identity["updates"]
            and metadata["catalog_sha256"] == identity["catalog_sha256"],
            "generic exporter changed training identity",
        )
        metadata.update(evidence)
        validate_metadata({key: metadata[key] for key in METADATA_FIELDS})
        _payload(output / "export/policy.f32.bin", metadata)
        stage = "final-input-recheck"
        preflight_gameplay(plan_path)
        receipt = {
            "format": "witcher3-genuine-shared-export-v1",
            "data_kind": "gameplay",
            "checkpoint_sha256": metadata["checkpoint_sha256"],
            "tensor_sha256": actual_tensor,
            "payload_sha256": metadata["export_sha256"],
            "metadata_identity_sha256": hashlib.sha256(
                _canonical(metadata)
            ).hexdigest(),
            "training_manifest_sha256": evidence["training_manifest_sha256"],
            "controller_source_sha256": evidence["controller_source_sha256"],
            "training_runtime_source_sha256": preflight["manifest"][
                "runtime_source_sha256"
            ],
            "training_implementation_scope": evidence["training_implementation_scope"],
            "serving_implementation_scope": evidence["serving_implementation_scope"],
            "trained_with_actionscript_simulator": False,
            "initialization": identity["initialization"],
            "actual_completed_updates": identity["updates"],
            "behavior_generation": identity["update_id"],
            "inputs": preflight["plan"]["inputs"],
            "corpus_receipts": preflight["plan"]["corpus_receipts"],
            "journal_identities": preflight["journal_specs"],
            "export_plan_sha256": _sha(plan_path),
            "checkpoint_rewritten": False,
            "tensor_identity_verified": True,
            "strength_evidence": False,
            "native_verified": False,
            "player_latency_verified": False,
            "timings_seconds": {
                "preflight": preflight_seconds,
                "torch_import": import_seconds,
                "total": time.perf_counter() - started,
            },
        }
        _write(output / "shared-export.json", receipt)
        metadata.update(
            shared_export_record="../shared-export.json",
            shared_export_sha256=_sha(output / "shared-export.json"),
        )
        _write(output / "export/metadata.json", metadata)
        verify_gameplay_binding(metadata, output / "shared-export.json")
        return receipt
    except BaseException as error:
        _write(
            output / "failure.json",
            {
                "stage": stage,
                "error_type": type(error).__name__,
                "partial_outputs_retained": True,
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_gameplay(args.plan, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
