#!/usr/bin/env python3
"""Create and consume inference-only model downloads without numerical packages.

``create_bundle({tier: export_directory}, destination, probes_path=...)`` reads
validated parameter exports and writes only their tensor bytes and restricted
metadata. ``load_bundle(directory)`` verifies every listed file and tensor.
``materialize(directory, local_catalog, destination)`` reconstructs the runtime
headers and returns a tier-to-PreparedPolicy mapping. The local catalog is never
copied into the download.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from scripts.policy_format import (
        OBSERVATION_PROFILES,
        catalog_identity,
        checkpoint_observation_profile,
        observation_profile,
        write_embed_header,
    )
except ModuleNotFoundError:
    from policy_format import (
        OBSERVATION_PROFILES,
        catalog_identity,
        checkpoint_observation_profile,
        observation_profile,
        write_embed_header,
    )

FORMAT = "witcher3-policy-bundle-v1"
PROBE_FORMAT = "witcher3-policy-probes-v1"
TIERS = ("easy", "normal", "hard")
WEIGHTS_FORMAT = "ieee754-float32-little-endian"
OBSERVATION_SCHEMA = "witcher3-gwent-seat-view-v1"
ACTION_SCHEMA = "witcher3-gwent-factorized-action-v1"
MAX_METADATA_BYTES = 1 << 20
MAX_WEIGHT_BYTES = 256 << 20
MAX_PROBE_BYTES = 256 << 20
METADATA_FIELDS = (
    "schema",
    "observation_schema_id",
    "observation_profile_id",
    "action_schema_id",
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
)


class PolicyBundleError(ValueError):
    """A model download or its local gameplay inputs are incompatible."""


@dataclass(frozen=True)
class PolicyBundle:
    manifest: dict[str, Any]
    metadata: dict[str, dict[str, Any]]
    weights_paths: dict[str, Path]
    probes_path: Path | None


@dataclass(frozen=True)
class PreparedPolicy:
    metadata: dict[str, Any]
    export_dir: Path
    weights_path: Path
    metadata_path: Path
    header_path: Path
    class_name: str


def parameter_shapes(
    observation_size: int, card_count: int, width: int, blocks: int
) -> dict[str, list[int]]:
    """The exact named tensor layout of the v1 policy/value architecture."""
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


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyBundleError(message)


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    _need(type(value) is int and minimum <= value <= maximum, f"invalid {name}")
    return value


def _sha(value: Any, name: str) -> str:
    _need(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"invalid {name} hash",
    )
    return value


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        _need(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise PolicyBundleError(f"non-finite JSON constant: {value}")


def _read_json(path: Path, limit: int = MAX_METADATA_BYTES) -> dict[str, Any]:
    _need(
        path.is_file() and not path.is_symlink(),
        f"missing or symlinked JSON: {path.name}",
    )
    _need(path.stat().st_size <= limit, f"JSON exceeds size limit: {path.name}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_pairs,
            parse_constant=_bad_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyBundleError(f"unreadable JSON: {path.name}") from exc
    _need(isinstance(value, dict), f"JSON object required: {path.name}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _validate_metadata(value: Any) -> dict[str, Any]:
    # Bundles published before explicit profiles used the generic encoder.
    if isinstance(value, dict):
        try:
            value = {**value, "observation_profile_id": observation_profile(value)}
        except ValueError as exc:
            raise PolicyBundleError(str(exc)) from exc
    _need(
        isinstance(value, dict) and set(value) == set(METADATA_FIELDS),
        "policy metadata has missing or unexpected fields",
    )
    _need(
        type(value["schema"]) is int and value["schema"] == 1,
        "unsupported policy metadata schema",
    )
    _need(
        value["observation_schema_id"] == OBSERVATION_SCHEMA,
        "unsupported observation schema",
    )
    _need(value["action_schema_id"] == ACTION_SCHEMA, "unsupported action schema")
    _need(value["weights_format"] == WEIGHTS_FORMAT, "unsupported weight format")
    for name in (
        "checkpoint_sha256",
        "export_sha256",
        "catalog_sha256",
        "weights_sha256",
    ):
        _sha(value[name], name)
    _need(
        value["export_sha256"] == value["weights_sha256"],
        "export and weight hashes disagree",
    )
    _integer(value["update_count"], "update_count", 0, (1 << 53) - 1)
    cards = _integer(value["card_count"], "card_count", 1, 10_000)
    width = _integer(value["width"], "width", 1, 2048)
    blocks = _integer(value["blocks"], "blocks", 1, 16)
    observations = _integer(value["observation_size"], "observation_size", 1, 1_000_000)
    # v1: 24 vocabulary features per card, 18 zones * 45 effects, and 64 globals.
    _need(
        observations == 24 * cards + 18 * 45 + 64,
        "observation_size does not match the v1 vocabulary",
    )
    expected = parameter_shapes(observations, cards, width, blocks)
    shapes = value["shapes"]
    offsets = value["offsets"]
    _need(
        isinstance(shapes, dict) and set(shapes) == set(expected),
        "tensor shapes do not match the v1 architecture",
    )
    _need(
        isinstance(offsets, dict) and set(offsets) == set(expected),
        "tensor offsets do not match the v1 architecture",
    )
    count = 0
    for name, shape in expected.items():
        supplied = shapes[name]
        _need(
            isinstance(supplied, list)
            and all(type(d) is int for d in supplied)
            and supplied == shape,
            f"invalid tensor shape: {name}",
        )
        _need(
            type(offsets[name]) is int and offsets[name] == count,
            f"invalid tensor offset: {name}",
        )
        count += math.prod(shape)
    _need(
        type(value["parameter_count"]) is int and value["parameter_count"] == count,
        "parameter_count does not match tensor layout",
    )
    _need(
        type(value["weight_bytes"]) is int
        and value["weight_bytes"] == count * 4 <= MAX_WEIGHT_BYTES,
        "weight_bytes does not match supported tensor layout",
    )
    normalized = {name: value[name] for name in METADATA_FIELDS}
    normalized["offsets"] = {name: offsets[name] for name in expected}
    normalized["shapes"] = expected
    return normalized


def _weights(path: Path, metadata: dict[str, Any]) -> bytes:
    _need(not path.parent.is_symlink(), "weight directory is a symlink")
    _need(
        path.is_file() and not path.is_symlink(), "missing or symlinked weight payload"
    )
    _need(
        path.stat().st_size == metadata["weight_bytes"], "weight payload size mismatch"
    )
    payload = path.read_bytes()
    _need(
        hashlib.sha256(payload).hexdigest() == metadata["weights_sha256"],
        "weight payload hash mismatch",
    )
    _need(
        all(math.isfinite(item[0]) for item in struct.iter_unpack("<f", payload)),
        "weight payload contains non-finite parameters",
    )
    return payload


def _weight_name(metadata: dict[str, Any]) -> str:
    return f"weights/{metadata['weights_sha256']}.f32.bin"


def _check_tree(root: Path, expected: set[str]) -> None:
    _need(
        root.is_dir() and not root.is_symlink(),
        "bundle directory is missing or symlinked",
    )
    actual = set()
    allowed_dirs = {"weights", "verification"}
    for path in root.rglob("*"):
        _need(not path.is_symlink(), "bundle contains a symlink")
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            actual.add(relative)
        else:
            _need(
                path.is_dir() and relative in allowed_dirs,
                f"unlisted bundle directory: {relative}",
            )
    _need(
        actual == expected,
        f"bundle contains missing or unlisted files: {sorted(actual ^ expected)}",
    )


def load_bundle(bundle_dir: Path) -> PolicyBundle:
    """Validate the complete download, including exact file and tensor inventories."""
    root = Path(bundle_dir)
    _need(
        root.is_dir() and not root.is_symlink(),
        "bundle directory is missing or symlinked",
    )
    manifest = _read_json(root / "manifest.json")
    _need(
        set(manifest) in ({"format", "tiers"}, {"format", "tiers", "verification"}),
        "unexpected bundle manifest fields",
    )
    _need(manifest["format"] == FORMAT, "unsupported policy bundle format")
    tiers = manifest["tiers"]
    _need(
        isinstance(tiers, dict) and set(tiers) == set(TIERS),
        "bundle must name exactly Easy, Normal and Hard tiers",
    )
    metadata = {}
    paths = {}
    expected = {"manifest.json"}
    checkpoint_metadata = {}
    for tier in TIERS:
        entry = tiers[tier]
        _need(
            isinstance(entry, dict)
            and set(entry)
            in (
                set(METADATA_FIELDS) | {"weights_path"},
                (set(METADATA_FIELDS) - {"observation_profile_id"}) | {"weights_path"},
            ),
            f"unexpected {tier} policy fields",
        )
        value = _validate_metadata(
            {key: entry[key] for key in METADATA_FIELDS if key in entry}
        )
        relative = _weight_name(value)
        _need(entry["weights_path"] == relative, "unsafe or noncanonical weight path")
        path = root / relative
        _weights(path, value)
        checkpoint = value["checkpoint_sha256"]
        _need(
            checkpoint not in checkpoint_metadata
            or checkpoint_metadata[checkpoint] == value,
            "one checkpoint names inconsistent policy metadata",
        )
        checkpoint_metadata[checkpoint] = value
        metadata[tier] = value
        paths[tier] = path
        expected.add(relative)
    catalog_contract = (
        "schema",
        "catalog_sha256",
        "observation_schema_id",
        "action_schema_id",
        "card_count",
        "observation_size",
    )
    _need(
        len({tuple(m[key] for key in catalog_contract) for m in metadata.values()})
        == 1,
        "policy tiers use different catalogs or vocabulary geometry",
    )
    probes_path = None
    if "verification" in manifest:
        descriptor = manifest["verification"]
        _need(
            isinstance(descriptor, dict)
            and set(descriptor) == {"format", "path", "sha256", "bytes"},
            "unexpected verification descriptor fields",
        )
        _need(
            descriptor["format"] == PROBE_FORMAT
            and descriptor["path"] == "verification/probes.json",
            "unsafe or unsupported verification artifact",
        )
        _sha(descriptor["sha256"], "verification")
        _integer(descriptor["bytes"], "verification bytes", 1, MAX_PROBE_BYTES)
        probes_path = root / descriptor["path"]
        _need(
            not probes_path.parent.is_symlink(), "verification directory is a symlink"
        )
        _need(
            probes_path.is_file() and not probes_path.is_symlink(),
            "missing or symlinked verification artifact",
        )
        _need(
            probes_path.stat().st_size == descriptor["bytes"],
            "verification artifact size mismatch",
        )
        _need(
            hashlib.sha256(probes_path.read_bytes()).hexdigest()
            == descriptor["sha256"],
            "verification artifact hash mismatch",
        )
        expected.add(descriptor["path"])
    _check_tree(root, expected)
    return PolicyBundle(manifest, metadata, paths, probes_path)


def create_bundle(
    tier_exports: Mapping[str, Path],
    output_dir: Path,
    *,
    probes_path: Path | None = None,
    observation_profile_id: str | None = None,
) -> dict[str, Any]:
    """Create a new sanitized bundle atomically, preserving any existing output."""
    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    _need(
        set(tier_exports) == set(TIERS), "supply exactly Easy, Normal and Hard exports"
    )
    entries = {}
    payloads = {}
    for tier in TIERS:
        root = Path(tier_exports[tier])
        _need(
            root.is_dir() and not root.is_symlink(),
            f"missing or symlinked {tier} export",
        )
        source = _read_json(root / "metadata.json")
        try:
            profile = checkpoint_observation_profile(
                source.get("checkpoint_sha256"), source, observation_profile_id
            )
        except ValueError as exc:
            raise PolicyBundleError(str(exc)) from exc
        source = {**source, "observation_profile_id": profile}
        _need(
            set(METADATA_FIELDS) - {"observation_profile_id"} <= set(source),
            f"missing {tier} metadata fields",
        )
        metadata = _validate_metadata(
            {name: source[name] for name in METADATA_FIELDS if name in source}
        )
        payload = _weights(root / "policy.f32.bin", metadata)
        relative = _weight_name(metadata)
        entries[tier] = {**metadata, "weights_path": relative}
        payloads[relative] = payload
    manifest = {"format": FORMAT, "tiers": entries}
    probe_bytes = None
    if probes_path is not None:
        probes_path = Path(probes_path)
        _need(
            probes_path.is_file() and not probes_path.is_symlink(),
            "missing or symlinked probe source",
        )
        _integer(probes_path.stat().st_size, "verification bytes", 1, MAX_PROBE_BYTES)
        probe_bytes = probes_path.read_bytes()
        manifest["verification"] = {
            "format": PROBE_FORMAT,
            "path": "verification/probes.json",
            "sha256": hashlib.sha256(probe_bytes).hexdigest(),
            "bytes": len(probe_bytes),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}-", dir=output.parent
    ) as temp:
        staging = Path(temp) / "bundle"
        (staging / "weights").mkdir(parents=True)
        for relative, payload in payloads.items():
            (staging / relative).write_bytes(payload)
        if probe_bytes is not None:
            (staging / "verification").mkdir()
            (staging / "verification/probes.json").write_bytes(probe_bytes)
        _write_json(staging / "manifest.json", manifest)
        load_bundle(staging)
        os.rename(staging, output)
    return manifest


def _local_tables(
    catalog: dict[str, Any], metadata: dict[str, Any]
) -> tuple[list[int], list[list[int]]]:
    try:
        actual_hash = catalog_identity(catalog)
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyBundleError("invalid local catalog") from exc
    _need(
        actual_hash == metadata["catalog_sha256"],
        "local catalog hash does not match the policy",
    )
    cards = catalog.get("cards")
    _need(
        isinstance(cards, list) and len(cards) == metadata["card_count"],
        "local catalog vocabulary size mismatch",
    )
    _need(
        metadata["schema"] == 1
        and metadata["observation_schema_id"] == OBSERVATION_SCHEMA
        and metadata["action_schema_id"] == ACTION_SCHEMA
        and metadata["observation_size"] == 24 * len(cards) + 18 * 45 + 64,
        "local catalog vocabulary schema or observation geometry mismatch",
    )
    ids = []
    effects = {}
    for card in cards:
        _need(isinstance(card, dict), "invalid local catalog card")
        card_id = _integer(card.get("id"), "catalog card id", 0, (1 << 31) - 1)
        _need(card_id not in effects, "duplicate local catalog card id")
        values = card.get("effects", [])
        _need(isinstance(values, list), "invalid local catalog effects")
        values = [_integer(effect, "catalog effect", 0, 44) for effect in values]
        ids.append(card_id)
        effects[card_id] = values
    ids.sort()
    return ids, [effects[card_id] for card_id in ids]


def materialize(
    bundle_dir: Path, catalog_path: Path, output_dir: Path
) -> dict[str, PreparedPolicy]:
    """Rebuild local metadata and headers after checking the player's catalog identity."""
    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    bundle = load_bundle(bundle_dir)
    catalog = _read_json(Path(catalog_path), MAX_PROBE_BYTES)
    tables = {tier: _local_tables(catalog, bundle.metadata[tier]) for tier in TIERS}
    output.parent.mkdir(parents=True, exist_ok=True)
    prepared = {}
    written = set()
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}-", dir=output.parent
    ) as temp:
        staging = Path(temp) / "exports"
        staging.mkdir()
        for tier in TIERS:
            metadata = dict(bundle.metadata[tier])
            card_ids, card_effects = tables[tier]
            checkpoint = metadata["checkpoint_sha256"]
            class_name = f"GwaiNetWeightsData_{checkpoint[:16]}"
            metadata.update(
                card_ids=card_ids,
                card_effects=card_effects,
                weights_class_name=class_name,
            )
            relative = Path(checkpoint)
            if checkpoint not in written:
                directory = staging / relative
                directory.mkdir()
                payload = _weights(bundle.weights_paths[tier], metadata)
                (directory / "policy.f32.bin").write_bytes(payload)
                _write_json(directory / "metadata.json", metadata)
                write_embed_header(
                    directory / f"{class_name}.as", payload, metadata, class_name
                )
                written.add(checkpoint)
            directory = output / relative
            prepared[tier] = PreparedPolicy(
                metadata,
                directory,
                directory / "policy.f32.bin",
                directory / "metadata.json",
                directory / f"{class_name}.as",
                class_name,
            )
        os.rename(staging, output)
    return prepared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    for tier in TIERS:
        create.add_argument(f"--{tier}", type=Path, required=True)
    create.add_argument("--output-dir", type=Path, required=True)
    create.add_argument("--probes", type=Path)
    create.add_argument("--observation-profile", choices=OBSERVATION_PROFILES)
    check = commands.add_parser("check")
    check.add_argument("bundle", type=Path)
    prepare = commands.add_parser("materialize")
    prepare.add_argument("bundle", type=Path)
    prepare.add_argument("--catalog", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            create_bundle(
                {tier: getattr(args, tier) for tier in TIERS},
                args.output_dir,
                probes_path=args.probes,
                observation_profile_id=args.observation_profile,
            )
        elif args.command == "check":
            load_bundle(args.bundle)
        else:
            materialize(args.bundle, args.catalog, args.output_dir)
    except (PolicyBundleError, OSError) as exc:
        parser.exit(1, f"policy bundle: {exc}\n")
    print("Policy bundle verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
