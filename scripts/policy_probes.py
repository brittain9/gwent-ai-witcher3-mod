"""Check exported policies with fictional vocabularies and reference outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.synthetic_policy_cases import synthetic_cases
except ModuleNotFoundError:
    from synthetic_policy_cases import synthetic_cases

FORMAT = "witcher3-policy-probes-v1"
VOCABULARY = "ordinal-v1"
COUNT = 200
SEED = 20261106
SOURCE = "fictional ordinal schema coverage (not gameplay or game card data)"
EXPECTED_FIELDS = {
    "action_id",
    "group_logits",
    "local_logits",
    "observation_sha256",
    "action_features",
    "action_group_ids",
    "group_features",
    "value",
    "outcome_probabilities",
}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate policy probe field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonfinite policy probe constant: {value}")


def _read_probes(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("policy probes must be a JSON object")
    return payload


def fictional_vocabulary(count: int) -> tuple[list[int], list[list[int]]]:
    if type(count) is not int or not 1 <= count <= 4096:
        raise ValueError("probe vocabulary size must be between 1 and 4096")
    return list(range(1, count + 1)), [
        sorted({index % 45, (index * 7 + 3) % 45}) for index in range(count)
    ]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")


def create_probes(
    checkpoints: dict[str, Path], catalog_path: Path, output_path: Path
) -> dict[str, Any]:
    """Produce reference checks; model dependencies are used only by this exporter."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from learning.catalog_identity import catalog_identity
    from learning.policy import checkpoint_policy
    from scripts.create_policy_fixtures import _logits_and_choices

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_sha = catalog_identity(catalog)
    card_ids, effects = fictional_vocabulary(len(catalog["cards"]))
    states = synthetic_cases(
        card_ids, COUNT, SEED, dict(zip(card_ids, effects, strict=True))
    )
    models = {}
    for checkpoint in checkpoints.values():
        checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        if checkpoint_sha in models:
            continue
        policy = checkpoint_policy(checkpoint, catalog, catalog_sha256=catalog_sha)
        predictions = [
            prediction
            for start in range(0, COUNT, 10)
            for prediction in _logits_and_choices(
                policy, states[start : start + 10], card_ids
            )
        ]
        header = {
            "fixture_schema": 1,
            "observation_schema_id": "witcher3-gwent-seat-view-v1",
            "action_schema_id": "witcher3-gwent-factorized-action-v1",
            "checkpoint_sha256": checkpoint_sha,
            "catalog_sha256": catalog_sha,
            "decision_source": SOURCE,
            "dev_game_count": 0,
            "synthetic_batch_size": 10,
            "rules_validated": False,
            "seed": SEED,
            "position_count": COUNT,
        }
        models[checkpoint_sha] = {
            "vocabulary": VOCABULARY,
            "card_ids": card_ids,
            "card_effects": effects,
            "header": header,
            "cases": [
                {
                    "position_id": index,
                    "observation": state["observation"],
                    "legal_actions": state["legal_actions"],
                    "expected": expected,
                }
                for index, (state, expected) in enumerate(
                    zip(states, predictions, strict=True)
                )
            ],
        }
    result = {"format": FORMAT, "models": models}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output_path, result)
    return {
        "models": len(models),
        "positions_per_model": COUNT,
        "decision_source": SOURCE,
    }


def _finite_numbers(value: Any) -> bool:
    if isinstance(value, list):
        return all(_finite_numbers(item) for item in value)
    return type(value) in (int, float) and math.isfinite(value)


def validate_model_probes(
    probes_path: Path, metadata: dict[str, Any]
) -> dict[str, Any]:
    """Validate that a model's checks contain only deterministic fictional cases."""
    payload = _read_probes(probes_path)
    if set(payload) != {"format", "models"} or payload["format"] != FORMAT:
        raise ValueError("unsupported policy probe format")
    checkpoint_sha = metadata["checkpoint_sha256"]
    probe = payload["models"].get(checkpoint_sha)
    if not isinstance(probe, dict) or set(probe) != {
        "vocabulary",
        "card_ids",
        "card_effects",
        "header",
        "cases",
    }:
        raise ValueError("policy probe model is missing or has unexpected fields")
    card_ids, effects = fictional_vocabulary(metadata["card_count"])
    if (
        probe["vocabulary"] != VOCABULARY
        or probe["card_ids"] != card_ids
        or probe["card_effects"] != effects
    ):
        raise ValueError("policy probes must use the fictional ordinal vocabulary")
    expected_header = {
        "fixture_schema": 1,
        "observation_schema_id": metadata["observation_schema_id"],
        "action_schema_id": metadata["action_schema_id"],
        "checkpoint_sha256": checkpoint_sha,
        "catalog_sha256": metadata["catalog_sha256"],
        "decision_source": SOURCE,
        "dev_game_count": 0,
        "synthetic_batch_size": 10,
        "rules_validated": False,
        "seed": SEED,
        "position_count": COUNT,
    }
    if probe["header"] != expected_header:
        raise ValueError("policy probe identity or schema differs from the model")
    states = synthetic_cases(
        card_ids, COUNT, SEED, dict(zip(card_ids, effects, strict=True))
    )
    if not isinstance(probe["cases"], list) or len(probe["cases"]) != COUNT:
        raise ValueError("policy probes must contain the complete fictional case set")
    for index, (case, state) in enumerate(zip(probe["cases"], states, strict=True)):
        if not isinstance(case, dict) or set(case) != {
            "position_id",
            "observation",
            "legal_actions",
            "expected",
        }:
            raise ValueError("policy probe case has unexpected fields")
        if (
            case["position_id"] != index
            or case["observation"] != state["observation"]
            or case["legal_actions"] != state["legal_actions"]
        ):
            raise ValueError(
                "policy probes differ from the deterministic fictional cases"
            )
        expected = case["expected"]
        if not isinstance(expected, dict) or set(expected) != EXPECTED_FIELDS:
            raise ValueError("policy probe outputs have unexpected fields")
        if expected["action_id"] not in {
            action["id"] for action in case["legal_actions"]
        }:
            raise ValueError("policy probe reference is not a listed action")
        digest = expected["observation_sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("policy probe observation hash is invalid")
        for key in EXPECTED_FIELDS - {"action_id", "observation_sha256"}:
            if not _finite_numbers(expected[key]):
                raise ValueError("policy probe outputs must contain finite numbers")
        if (
            len(expected["local_logits"]) != len(case["legal_actions"])
            or len(expected["outcome_probabilities"]) != 3
            or len(expected["group_logits"]) != len(expected["group_features"])
            or len(expected["action_group_ids"]) != len(case["legal_actions"])
            or len(expected["action_features"]) != len(case["legal_actions"])
        ):
            raise ValueError("policy probe output dimensions differ from its actions")
    return probe


def validate_bundle_probes(
    probes_path: Path, tier_metadata: dict[str, dict[str, Any]]
) -> None:
    """Audit every bundled model's checks without generating local files."""
    payload = _read_probes(probes_path)
    expected = {item["checkpoint_sha256"] for item in tier_metadata.values()}
    if set(payload.get("models", {})) != expected:
        raise ValueError("policy probes must name exactly the bundled models")
    for metadata in tier_metadata.values():
        validate_model_probes(probes_path, metadata)


def prepare_checks(
    probes_path: Path, metadata: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Materialize a validated fictional replay beside the local runtime header."""
    probe = validate_model_probes(probes_path, metadata)
    card_ids, effects = fictional_vocabulary(metadata["card_count"])
    output_dir.mkdir(parents=True, exist_ok=True)
    replay_metadata = dict(metadata, card_ids=card_ids, card_effects=effects)
    metadata_path = output_dir / "probe-metadata.json"
    fixture_path = output_dir / "portable-probes.jsonl"
    _write_json(metadata_path, replay_metadata)
    with fixture_path.open("w", encoding="utf-8") as handle:
        for item in [probe["header"], *probe["cases"]]:
            handle.write(
                json.dumps(item, separators=(",", ":"), allow_nan=False) + "\n"
            )
    return {
        "path": str(fixture_path),
        "metadata_path": str(metadata_path),
        "decision_source": SOURCE,
        "count": COUNT,
        "dev_game_count": 0,
        "synthetic_batch_size": 10,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpoint", action="append", required=True, metavar="TIER=PATH"
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checkpoints = {}
    for assignment in args.checkpoint:
        tier, separator, value = assignment.partition("=")
        if not separator or tier not in {"easy", "normal", "hard"} or not value:
            parser.error("checkpoint must be easy=PATH, normal=PATH or hard=PATH")
        checkpoints[tier] = Path(value)
    print(json.dumps(create_probes(checkpoints, args.catalog, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
