"""Verify the policy data actually embedded in the compiled Gwent screen."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any

try:
    from scripts.policy_format import observation_profile
except ModuleNotFoundError:
    from policy_format import observation_profile

CONSTANTS = {
    "CHECKPOINT_SHA256": "checkpoint_sha256",
    "UPDATE_COUNT": "update_count",
    "EXPORT_SHA256": "export_sha256",
    "CATALOG_SHA256": "catalog_sha256",
    "WEIGHT_COUNT": "parameter_count",
    "OBSERVATION_SIZE": "observation_size",
    "OBSERVATION_PROFILE_ID": "observation_profile_id",
    "CARD_COUNT": "card_count",
    "WIDTH": "width",
    "BLOCKS": "blocks",
    "OFFSETS": "offsets",
    "CARD_IDS": "card_ids",
    "CARD_EFFECTS": "card_effects",
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate compiled policy key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid compiled policy number: {value}")


def _constant(source: str, name: str) -> Any:
    matches = re.findall(
        rf"\bpublic\s+static\s+const\s+{re.escape(name)}\s*:\s*\w+\s*=\s*([^;]+);",
        source,
    )
    if len(matches) != 1:
        raise ValueError(f"compiled policy must declare exactly one {name}")
    try:
        return json.loads(
            matches[0],
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except ValueError as error:
        raise ValueError(f"compiled policy {name} is not a literal value") from error


def verify(
    helpers: dict[str, str], expected: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Compare decoded compiled tensors and all encoding tables with local inputs."""
    actual_classes = {
        name for name in helpers if name.startswith("GwaiNetWeightsData_")
    }
    if actual_classes != set(expected):
        raise ValueError(
            "compiled policy class inventory differs from the selected models"
        )
    checked = []
    for name, metadata in expected.items():
        metadata = {**metadata, "observation_profile_id": observation_profile(metadata)}
        source = helpers[name]
        if _constant(source, "EMBEDDED") is not True:
            raise ValueError(f"compiled policy {name} is not embedded")
        for constant, field in CONSTANTS.items():
            value = _constant(source, constant)
            if json.dumps(value, sort_keys=True, allow_nan=False) != json.dumps(
                metadata[field], sort_keys=True, allow_nan=False
            ):
                raise ValueError(f"compiled policy {name} differs in {constant}")
        chunks = _constant(source, "BASE64")
        if (
            not isinstance(chunks, list)
            or not chunks
            or any(type(item) is not str for item in chunks)
        ):
            raise ValueError(f"compiled policy {name} has invalid weight chunks")
        try:
            decoded = [base64.b64decode(chunk, validate=True) for chunk in chunks]
            if any(not chunk or len(chunk) % 4 != 0 for chunk in decoded):
                raise ValueError("compiled chunks must each hold whole float32 values")
            payload = b"".join(decoded)
        except (ValueError, binascii.Error) as error:
            raise ValueError(
                f"compiled policy {name} has invalid base64 weights"
            ) from error
        if (
            len(payload) != metadata["weight_bytes"]
            or len(payload) != metadata["parameter_count"] * 4
            or hashlib.sha256(payload).hexdigest() != metadata["export_sha256"]
        ):
            raise ValueError(
                f"compiled policy {name} tensor bytes differ from the selected model"
            )
        checked.append(
            {"class": name, "sha256": metadata["export_sha256"], "bytes": len(payload)}
        )
    return {"verified": True, "models": checked}
