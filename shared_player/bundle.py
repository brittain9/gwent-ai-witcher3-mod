"""One inference export, one weight payload, and explicit controller settings.

This module uses only the Python standard library. It never loads a training
checkpoint, imports numerical packages, chooses a replacement model or opens a
game installation. Numerical replay and compiled validation remain separate
required build gates; creating a bundle is not release acceptance.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT = "witcher3-shared-policy-bundle-v1"
CONTROLLER_CONTRACT = "deployed-controller-v1"
COUNTER_CONTRACT = "completed-primary-turns-v1"
SELECTION_RULE = "served-group-first-v1"
INFORMATION_PROTOCOL = "seat-view-v1"
PROFILES = frozenset({"full-faction-v1", "faction-unknown-v1"})
METADATA_FIELDS = frozenset(
    {
        "schema",
        "data_kind",
        "observation_schema_id",
        "observation_profile_id",
        "action_schema_id",
        "controller_contract",
        "counter_contract",
        "selection_rule",
        "information_protocol",
        "controller_source_sha256",
        "training_manifest_sha256",
        "checkpoint_sha256",
        "update_count",
        "export_sha256",
        "catalog_sha256",
        "observation_size",
        "card_count",
        "width",
        "blocks",
        "parameter_count",
        "weights_format",
        "weight_bytes",
        "weights_sha256",
        "offsets",
        "shapes",
    }
)
MAX_METADATA_BYTES = 1 << 20
MAX_WEIGHT_BYTES = 256 << 20


class SharedBundleError(ValueError):
    """A shared artifact violates its explicit identity or inference contract."""


@dataclass(frozen=True)
class SharedBundle:
    manifest: dict[str, Any]
    metadata: dict[str, Any]
    weights_path: Path


@dataclass(frozen=True)
class PreparedSharedPolicy:
    metadata: dict[str, Any]
    export_dir: Path
    weights_path: Path
    metadata_path: Path
    header_path: Path
    controller_path: Path
    class_name: str


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise SharedBundleError(message)


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    _need(type(value) is int and minimum <= value <= maximum, f"invalid {label}")
    return value


def _sha(value: Any, label: str) -> str:
    _need(
        type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"invalid {label}",
    )
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _need(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise SharedBundleError(f"non-finite JSON constant: {value}")


def _json(path: Path, limit: int = MAX_METADATA_BYTES) -> dict[str, Any]:
    _need(path.is_file() and not path.is_symlink(), f"missing or symlinked {path.name}")
    _need(path.stat().st_size <= limit, f"oversized {path.name}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object,
            parse_constant=_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SharedBundleError(f"invalid JSON: {path.name}") from error
    _need(isinstance(value, dict), f"expected JSON object: {path.name}")
    return value


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def parameter_shapes(
    observation_size: int, card_count: int, width: int, blocks: int
) -> dict[str, list[int]]:
    """Existing policy/value tensor geometry, preserving the numerical format."""
    shapes = {
        "input.weight": [width, observation_size],
        "input.bias": [width],
        "input_norm.weight": [width],
        "input_norm.bias": [width],
    }
    for index in range(blocks):
        prefix = f"blocks.{index}."
        shapes.update(
            {
                prefix + "norm.weight": [width],
                prefix + "norm.bias": [width],
                prefix + "first.weight": [width, width],
                prefix + "first.bias": [width],
                prefix + "second.weight": [width, width],
                prefix + "second.bias": [width],
            }
        )
    shapes.update(
        {
            "kind_embedding.weight": [6, 24],
            "card_embedding.weight": [card_count + 1, 48],
            "row_embedding.weight": [4, 8],
            "action_projection.weight": [width, 176],
            "action_projection.bias": [width],
            "group_projection.weight": [width, 72],
            "group_projection.bias": [width],
            "local_actor.0.weight": [128, 2 * width],
            "local_actor.0.bias": [128],
            "local_actor.2.weight": [1, 128],
            "local_actor.2.bias": [1],
            "group_actor.0.weight": [128, 2 * width],
            "group_actor.0.bias": [128],
            "group_actor.2.weight": [1, 128],
            "group_actor.2.bias": [1],
            "value_head.0.weight": [128, width],
            "value_head.0.bias": [128],
            "value_head.2.weight": [3, 128],
            "value_head.2.bias": [3],
        }
    )
    return shapes


def validate_metadata(value: Any) -> dict[str, Any]:
    _need(isinstance(value, dict), "model metadata must be an object")
    missing = METADATA_FIELDS - set(value)
    _need(not missing, f"missing model fields: {sorted(missing)}")
    _need(set(value) == METADATA_FIELDS, "unexpected model metadata fields")
    _need(type(value["schema"]) is int and value["schema"] == 1, "unsupported schema")
    contracts = {
        "observation_schema_id": "witcher3-gwent-seat-view-v1",
        "action_schema_id": "witcher3-gwent-factorized-action-v1",
        "controller_contract": CONTROLLER_CONTRACT,
        "counter_contract": COUNTER_CONTRACT,
        "selection_rule": SELECTION_RULE,
        "information_protocol": INFORMATION_PROTOCOL,
        "weights_format": "ieee754-float32-little-endian",
    }
    _need(value["data_kind"] in {"gameplay", "inference-only"}, "unsupported data_kind")
    for field, expected in contracts.items():
        _need(value[field] == expected, f"unsupported {field}")
    _need(
        type(value["observation_profile_id"]) is str
        and value["observation_profile_id"] in PROFILES,
        "unsupported observation_profile_id",
    )
    for field in (
        "controller_source_sha256",
        "training_manifest_sha256",
        "checkpoint_sha256",
        "export_sha256",
        "catalog_sha256",
        "weights_sha256",
    ):
        if (
            field == "training_manifest_sha256"
            and value["data_kind"] == "inference-only"
        ):
            _need(
                value[field] == "unrecorded",
                "inference export cannot claim training provenance",
            )
        else:
            _sha(value[field], field)
    _need(
        value["export_sha256"] == value["weights_sha256"],
        "export and weight hashes differ",
    )
    _integer(value["update_count"], "update_count", 0, (1 << 53) - 1)
    cards = _integer(value["card_count"], "card_count", 1, 10_000)
    width = _integer(value["width"], "width", 1, 2048)
    blocks = _integer(value["blocks"], "blocks", 1, 16)
    observations = _integer(value["observation_size"], "observation_size", 1, 1_000_000)
    _need(observations == 24 * cards + 18 * 45 + 64, "observation geometry differs")
    expected_shapes = parameter_shapes(observations, cards, width, blocks)
    shapes, offsets = value["shapes"], value["offsets"]
    _need(
        isinstance(shapes, dict) and set(shapes) == set(expected_shapes),
        "tensor shape inventory differs",
    )
    _need(
        isinstance(offsets, dict) and set(offsets) == set(expected_shapes),
        "tensor offset inventory differs",
    )
    count = 0
    for name, shape in expected_shapes.items():
        actual = shapes[name]
        _need(
            isinstance(actual, list)
            and all(type(item) is int for item in actual)
            and actual == shape,
            f"invalid tensor shape: {name}",
        )
        _need(
            type(offsets[name]) is int and offsets[name] == count,
            f"invalid tensor offset: {name}",
        )
        count += math.prod(shape)
    _need(
        type(value["parameter_count"]) is int and value["parameter_count"] == count,
        "parameter count differs",
    )
    _need(
        type(value["weight_bytes"]) is int
        and value["weight_bytes"] == count * 4 <= MAX_WEIGHT_BYTES,
        "weight byte count differs",
    )
    normalized = copy.deepcopy(value)
    normalized["shapes"] = expected_shapes
    normalized["offsets"] = {name: offsets[name] for name in expected_shapes}
    return normalized


def validate_controller(value: Any) -> dict[str, Any]:
    _need(
        isinstance(value, dict)
        and set(value)
        in (
            {"enabled", "mode", "difficulties"},
            {"enabled", "mode", "difficulties", "terminal_pass_v1"},
        ),
        "controller needs exactly enabled, mode and difficulties",
    )
    _need(type(value["enabled"]) is bool, "controller enabled must be boolean")
    _need(
        "terminal_pass_v1" not in value or type(value["terminal_pass_v1"]) is bool,
        "terminal pass flag must be boolean",
    )
    _need(value["mode"] == "policy-only", "only policy-only is implemented")
    levels = value["difficulties"]
    _need(
        isinstance(levels, dict) and set(levels) == {"1", "2", "3"},
        "controller must configure native difficulties 1, 2 and 3",
    )
    fields = {"temperature", "planning_worlds", "rollout_primary_turns", "deadline_ms"}
    for level, settings in levels.items():
        _need(
            isinstance(settings, dict) and set(settings) == fields,
            f"invalid native difficulty settings: {level}",
        )
        temperature = settings["temperature"]
        _need(
            type(temperature) in (int, float)
            and math.isfinite(temperature)
            and temperature >= 0,
            "temperature must be finite and nonnegative",
        )
        for field in fields - {"temperature"}:
            _need(
                type(settings[field]) is int and settings[field] == 0,
                f"policy-only refuses nonzero {field}",
            )
    return copy.deepcopy(value)


def _payload(path: Path, metadata: dict[str, Any]) -> bytes:
    _need(
        path.is_file() and not path.is_symlink() and not path.parent.is_symlink(),
        "missing or symlinked weights",
    )
    _need(
        path.stat().st_size == metadata["weight_bytes"], "weight payload size differs"
    )
    payload = path.read_bytes()
    _need(
        hashlib.sha256(payload).hexdigest() == metadata["weights_sha256"],
        "weight payload hash differs",
    )
    _need(
        all(math.isfinite(item[0]) for item in struct.iter_unpack("<f", payload)),
        "non-finite weight",
    )
    return payload


def load_bundle(directory: Path) -> SharedBundle:
    root = Path(directory)
    _need(
        root.is_dir() and not root.is_symlink(), "bundle root is missing or symlinked"
    )
    manifest = _json(root / "manifest.json")
    _need(
        set(manifest) == {"format", "model", "controller"}, "unexpected bundle fields"
    )
    _need(manifest["format"] == FORMAT, "unsupported shared bundle format")
    model = manifest["model"]
    _need(
        isinstance(model, dict) and set(model) == METADATA_FIELDS | {"weights_path"},
        "bundle must contain one complete shared model",
    )
    metadata = validate_metadata({name: model[name] for name in METADATA_FIELDS})
    validate_controller(manifest["controller"])
    relative = f"weights/{metadata['weights_sha256']}.f32.bin"
    _need(model["weights_path"] == relative, "noncanonical shared weight path")
    actual = set()
    for path in root.rglob("*"):
        _need(not path.is_symlink(), "bundle contains a symlink")
        name = path.relative_to(root).as_posix()
        if path.is_file():
            actual.add(name)
        else:
            _need(
                path.is_dir() and name == "weights",
                f"unlisted bundle directory: {name}",
            )
    _need(
        actual == {"manifest.json", relative}, "missing or unlisted shared bundle files"
    )
    weights_path = root / relative
    _payload(weights_path, metadata)
    return SharedBundle(copy.deepcopy(manifest), metadata, weights_path)


def create_bundle(
    export: Path, output_dir: Path, *, controller: dict[str, Any]
) -> dict[str, Any]:
    output, source = Path(output_dir), Path(export)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    _need(
        source.is_dir() and not source.is_symlink(),
        "export root is missing or symlinked",
    )
    source_metadata = _json(source / "metadata.json")
    missing = METADATA_FIELDS - set(source_metadata)
    _need(not missing, f"missing model fields: {sorted(missing)}")
    metadata = validate_metadata(
        {name: source_metadata[name] for name in METADATA_FIELDS}
    )
    settings = validate_controller(controller)
    payload = _payload(source / "policy.f32.bin", metadata)
    relative = f"weights/{metadata['weights_sha256']}.f32.bin"
    manifest = {
        "format": FORMAT,
        "model": {**metadata, "weights_path": relative},
        "controller": settings,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "bundle"
        (staging / "weights").mkdir(parents=True)
        (staging / relative).write_bytes(payload)
        _write(staging / "manifest.json", manifest)
        load_bundle(staging)
        os.rename(staging, output)
    return copy.deepcopy(manifest)


def _catalog_tables(
    catalog: dict[str, Any], metadata: dict[str, Any]
) -> tuple[list[int], list[list[int]]]:
    try:
        content = {name: catalog[name] for name in ("schema_version", "cards", "decks")}
        content["runtime_mappings"] = catalog.get("runtime_mappings", {})
        digest = hashlib.sha256(
            json.dumps(
                content,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()
    except (KeyError, TypeError, ValueError) as error:
        raise SharedBundleError("invalid local catalog") from error
    _need(digest == metadata["catalog_sha256"], "local catalog hash differs")
    cards = catalog.get("cards")
    _need(
        isinstance(cards, list) and len(cards) == metadata["card_count"],
        "local catalog vocabulary size differs",
    )
    by_id = {}
    for card in cards:
        _need(isinstance(card, dict), "invalid local catalog card")
        card_id = _integer(card.get("id"), "catalog card ID", 0, (1 << 31) - 1)
        _need(card_id not in by_id, "duplicate local catalog card ID")
        effects = card.get("effects", [])
        _need(isinstance(effects, list), "invalid local card effects")
        by_id[card_id] = [_integer(effect, "card effect", 0, 44) for effect in effects]
    ids = sorted(by_id)
    return ids, [by_id[card_id] for card_id in ids]


def _header(payload: bytes, metadata: dict[str, Any], class_name: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    constants = {
        "EMBEDDED": True,
        "DATA_KIND": metadata["data_kind"],
        "CHECKPOINT_SHA256": metadata["checkpoint_sha256"],
        "UPDATE_COUNT": metadata["update_count"],
        "EXPORT_SHA256": metadata["export_sha256"],
        "CATALOG_SHA256": metadata["catalog_sha256"],
        "WEIGHT_COUNT": metadata["parameter_count"],
        "OBSERVATION_SIZE": metadata["observation_size"],
        "OBSERVATION_PROFILE_ID": metadata["observation_profile_id"],
        "CARD_COUNT": metadata["card_count"],
        "WIDTH": metadata["width"],
        "BLOCKS": metadata["blocks"],
        "OFFSETS": metadata["offsets"],
        "CARD_IDS": metadata["card_ids"],
        "CARD_EFFECTS": metadata["card_effects"],
        "BASE64": [
            encoded[start : start + 48_000] for start in range(0, len(encoded), 48_000)
        ],
    }
    lines = [f"internal class {class_name}", "{"]
    for name, value in constants.items():
        kind = (
            "Boolean"
            if type(value) is bool
            else "Number"
            if type(value) is int and value > (1 << 31) - 1
            else "int"
            if type(value) is int
            else "String"
            if type(value) is str
            else "Array"
            if isinstance(value, list)
            else "Object"
        )
        literal = json.dumps(value, separators=(",", ":"), allow_nan=False)
        lines.append(f"   public static const {name}:{kind} = {literal};")
    return "\n".join([*lines, "}", ""])


def materialize(
    bundle_dir: Path, catalog_path: Path, output_dir: Path
) -> PreparedSharedPolicy:
    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    loaded = load_bundle(bundle_dir)
    catalog = _json(Path(catalog_path), 256 << 20)
    ids, effects = _catalog_tables(catalog, loaded.metadata)
    class_name = f"GwaiNetWeightsData_{loaded.metadata['checkpoint_sha256'][:16]}"
    metadata = {
        **loaded.metadata,
        "card_ids": ids,
        "card_effects": effects,
        "weights_class_name": class_name,
    }
    payload = _payload(loaded.weights_path, metadata)
    source = _header(payload, metadata, class_name)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "model"
        staging.mkdir()
        (staging / "policy.f32.bin").write_bytes(payload)
        _write(staging / "metadata.json", metadata)
        _write(staging / "controller.json", loaded.manifest["controller"])
        (staging / f"{class_name}.as").write_text(source, encoding="utf-8")
        os.rename(staging, output)
    return PreparedSharedPolicy(
        metadata,
        output,
        output / "policy.f32.bin",
        output / "metadata.json",
        output / f"{class_name}.as",
        output / "controller.json",
        class_name,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--export", type=Path, required=True)
    create.add_argument("--controller", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("bundle", type=Path)
    prepare = commands.add_parser("materialize")
    prepare.add_argument("bundle", type=Path)
    prepare.add_argument("--catalog", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            create_bundle(args.export, args.output, controller=_json(args.controller))
        elif args.command == "check":
            load_bundle(args.bundle)
        else:
            materialize(args.bundle, args.catalog, args.output)
    except (SharedBundleError, OSError) as error:
        parser.exit(1, f"shared model: {error}\n")
    print(
        "Single-model artifact verified; numerical/native acceptance remains separate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
