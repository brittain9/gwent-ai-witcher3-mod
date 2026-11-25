#!/usr/bin/env python3
"""Export a frozen Witcher 3 policy as float32 weights and an AS3 embed header."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from learning.catalog_identity import catalog_identity  # noqa: E402
from learning.encoding import (  # noqa: E402
    ACTION_SCHEMA_ID,
    OBSERVATION_SCHEMA_ID,
    observation_size,
)
from learning.model import AtaraxosPolicyValue  # noqa: E402
from scripts.policy_format import (  # noqa: E402
    OBSERVATION_PROFILES,
    checkpoint_observation_profile,
    write_embed_header,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHECKPOINT = PROJECT_ROOT / ".runtime/models/hard.pt"
DEFAULT_CATALOG = PROJECT_ROOT / ".runtime/catalog.json"
DEFAULT_OUTPUT = PROJECT_ROOT / ".runtime/policy-as3"


def checkpoint_update_count(checkpoint: dict) -> int | None:
    """Report unknown history for an explicit, attested profile initialization."""
    identity = checkpoint.get("identity", {})
    if checkpoint.get("format") == "faction-profile-initialization-v1":
        source = checkpoint.get("source_checkpoint", {})
        proof = checkpoint.get("derivation", {})
        digest = re.compile(r"[0-9a-f]{64}\Z")
        if not (
            identity.get("data_kind") == "profile-migration-initialization"
            and "updates" not in identity
            and type(identity.get("optimizer_steps_performed")) is int
            and identity["optimizer_steps_performed"] == 0
            and identity.get("trained_with_full_profile") is False
            and identity.get("source_profile") == "faction-unknown-v1"
            and identity.get("observation_profile_id") == "full-faction-v1"
            and checkpoint.get("model_config", {}).get("observation_profile_id")
            == "full-faction-v1"
            and source.get("source_profile") == "faction-unknown-v1"
            and source.get("role") in {"legacy-baseline", "shared-onpolicy"}
            and isinstance(source.get("sha256"), str)
            and digest.fullmatch(source["sha256"])
            and isinstance(identity.get("manifest_sha256"), str)
            and digest.fullmatch(identity["manifest_sha256"])
            and proof.get("operation") == "faction-profile-initialization-v1"
            and proof.get("source_profile") == "faction-unknown-v1"
            and proof.get("target_profile") == "full-faction-v1"
            and proof.get("architecture_changed") is False
            and isinstance(identity.get("tensor_sha256"), str)
            and digest.fullmatch(identity["tensor_sha256"])
            and isinstance(identity.get("source_tensor_sha256"), str)
            and digest.fullmatch(identity["source_tensor_sha256"])
            and proof.get("derived_tensor_sha256") == identity["tensor_sha256"]
            and proof.get("source_tensor_sha256") == identity["source_tensor_sha256"]
        ):
            raise ValueError("invalid profile initialization provenance")
        return None
    updates = identity.get("updates")
    if type(updates) is not int or updates < 0:
        raise ValueError(
            "checkpoint identity must contain a non-negative integer update count"
        )
    return updates


def export(
    checkpoint_path: Path,
    catalog_path: Path,
    output_dir: Path,
    *,
    class_name: str = "GwaiNetWeightsData",
    observation_profile_id: str | None = None,
) -> dict[str, object]:
    if not re.fullmatch(r"GwaiNetWeightsData(?:_[A-Za-z0-9]+)?", class_name):
        raise ValueError(
            "class name must be GwaiNetWeightsData with an optional safe suffix"
        )
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    catalog_sha256 = catalog_identity(catalog)
    identity = checkpoint.get("identity", {})
    config = checkpoint.get("model_config", {})
    checkpoint_sha256 = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    profile = checkpoint_observation_profile(
        checkpoint_sha256, config, observation_profile_id
    )
    if identity.get("catalog_sha256") != catalog_sha256:
        raise ValueError("checkpoint catalog hash does not match the selected catalog")
    if config.get("observation_schema_id") != OBSERVATION_SCHEMA_ID:
        raise ValueError("checkpoint observation schema is not v1")
    if config.get("action_schema_id") != ACTION_SCHEMA_ID:
        raise ValueError("checkpoint action schema is not v1")

    card_ids = sorted(int(card["id"]) for card in catalog["cards"])
    if identity.get("card_ids") != card_ids or int(config.get("card_count", -1)) != len(
        card_ids
    ):
        raise ValueError(
            "checkpoint card vocabulary does not match the selected catalog"
        )
    expected_size = observation_size(card_ids)
    if int(config.get("observation_size", -1)) != expected_size:
        raise ValueError("checkpoint observation size does not match schema v1")

    model = AtaraxosPolicyValue(
        expected_size,
        len(card_ids),
        width=int(config["width"]),
        blocks=int(config["blocks"]),
    )
    model.load_state_dict(checkpoint["policy_state"], strict=True)
    model.eval()

    state = model.state_dict()
    payload_parts: list[bytes] = []
    parameter_count = 0
    offsets: dict[str, int] = {}
    shapes: dict[str, list[int]] = {}
    for name, tensor in state.items():
        value = (
            tensor.detach().to(dtype=torch.float32, device="cpu").contiguous().view(-1)
        )
        if not torch.isfinite(value).all():
            raise ValueError(f"checkpoint tensor {name} contains a non-finite weight")
        offsets[name] = parameter_count
        shapes[name] = list(tensor.shape)
        payload_parts.append(value.numpy().astype("<f4", copy=False).tobytes())
        parameter_count += value.numel()

    # AS3 ByteArray.readFloat uses IEEE-754 little endian. Keeping float32
    # preserves the checkpoint values; the runtime Vector.<Number> expands
    # these values to Number as it loads them.
    payload = b"".join(payload_parts)
    updates = checkpoint_update_count(checkpoint)
    card_effects = [
        list(map(int, card.get("effects", [])))
        for card in sorted(catalog["cards"], key=lambda item: int(item["id"]))
    ]
    metadata: dict[str, object] = {
        "schema": 1,
        "observation_schema_id": OBSERVATION_SCHEMA_ID,
        "observation_profile_id": profile,
        "action_schema_id": ACTION_SCHEMA_ID,
        "checkpoint_sha256": checkpoint_sha256,
        "update_count": updates,
        "export_sha256": hashlib.sha256(payload).hexdigest(),
        "catalog_sha256": catalog_sha256,
        "run_id": identity.get("run_id"),
        "card_ids": card_ids,
        "card_effects": card_effects,
        "observation_size": expected_size,
        "card_count": len(card_ids),
        "width": int(config["width"]),
        "blocks": int(config["blocks"]),
        "parameter_count": parameter_count,
        "weights_format": "ieee754-float32-little-endian",
        "weight_bytes": len(payload),
        "weights_sha256": hashlib.sha256(payload).hexdigest(),
        "weights_class_name": class_name,
        "offsets": offsets,
        "shapes": shapes,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    weights_path = output_dir / "policy.f32.bin"
    weights_path.write_bytes(payload)
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    write_embed_header(output_dir / f"{class_name}.as", payload, metadata, class_name)
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--class-name", default="GwaiNetWeightsData")
    parser.add_argument("--observation-profile", choices=OBSERVATION_PROFILES)
    args = parser.parse_args(argv)
    metadata = export(
        args.checkpoint,
        args.catalog,
        args.output_dir,
        class_name=args.class_name,
        observation_profile_id=args.observation_profile,
    )
    print(
        json.dumps(
            {
                key: metadata[key]
                for key in (
                    "checkpoint_sha256",
                    "observation_profile_id",
                    "update_count",
                    "export_sha256",
                    "catalog_sha256",
                    "parameter_count",
                    "weight_bytes",
                    "weights_sha256",
                )
            },
            indent=2,
        )
    )
    print(
        f"AS3 embed: {args.output_dir / (str(metadata['weights_class_name']) + '.as')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
