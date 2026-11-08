#!/usr/bin/env python3
"""Deterministic synthetic schema coverage, without an engine or game records.

The action lists are schema-shaped test inputs. They do not assert native-game
legality, gameplay strength, self-play coverage or rules parity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from learning.catalog_identity import catalog_identity
from learning.encoding import ACTION_SCHEMA_ID, OBSERVATION_SCHEMA_ID, encode_batch
from learning.policy import checkpoint_policy, served_argmax
from scripts.policy_format import OBSERVATION_PROFILES
from scripts.synthetic_policy_cases import PENDING_KINDS as PENDING_KINDS
from scripts.synthetic_policy_cases import synthetic_cases as synthetic_cases

DECISION_SOURCE = "synthetic schema coverage (not gameplay or rules parity)"
DEFAULT_CHECKPOINT = PROJECT_ROOT / ".runtime/learner-pilot-v5-checkpoint.pt"
DEFAULT_CATALOG = PROJECT_ROOT / ".runtime/catalog.json"
DEFAULT_OUTPUT = PROJECT_ROOT / ".runtime/policy-as3-fixtures/synthetic.jsonl"


def _logits_and_choices(
    policy: Any, states: list[dict[str, Any]], card_ids: list[int]
) -> list[dict[str, Any]]:
    encoded = encode_batch(
        states, card_ids, observation_profile_id=policy.observation_profile_id
    )
    with torch.inference_mode():
        trunk, group_tensor, local_tensor = policy._model.logits(
            torch.from_numpy(encoded.observations),
            torch.from_numpy(encoded.action_features),
            torch.from_numpy(encoded.group_features),
        )
        outcomes = torch.softmax(policy._model.value_head(trunk), dim=-1).numpy()
    groups, locals_ = group_tensor.numpy(), local_tensor.numpy()
    predictions = []
    for row, actions in enumerate(encoded.action_objects):
        action_count, group_count = len(actions), int(encoded.group_mask[row].sum())
        group_logits, local_logits = (
            groups[row, :group_count].tolist(),
            locals_[row, :action_count].tolist(),
        )
        group_ids = encoded.action_group_ids[row, :action_count].tolist()
        group_features = encoded.group_features[row, :group_count].tolist()
        action_features = encoded.action_features[row, :action_count].tolist()
        chosen = served_argmax(
            group_logits, local_logits, group_ids, group_features, action_features
        )
        probabilities = [float(value) for value in outcomes[row]]
        predictions.append(
            {
                "action_id": actions[chosen]["id"],
                "group_logits": group_logits,
                "local_logits": local_logits,
                "observation_sha256": hashlib.sha256(
                    encoded.observations[row].astype("<f4", copy=False).tobytes()
                ).hexdigest(),
                "action_features": action_features,
                "action_group_ids": group_ids,
                "group_features": group_features,
                "value": probabilities[1] * 0.5 + probabilities[2],
                "outcome_probabilities": probabilities,
            }
        )
    return predictions


def create_fixtures(
    *,
    checkpoint_path: Path,
    catalog_path: Path,
    output_path: Path,
    count: int = 200,
    seed: int = 20261025,
    env_count: int = 10,
    observation_profile_id: str | None = None,
) -> dict[str, Any]:
    """Write synthetic coverage with real checkpoint policy/value reference outputs."""
    if type(count) is not int or count < 1:
        raise ValueError("synthetic fixture count must be positive")
    if type(env_count) is not int or not 1 <= env_count <= 10:
        raise ValueError("synthetic reference batch size must be between 1 and 10")
    if type(seed) is not int:
        raise ValueError("synthetic fixture seed must be an integer")
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    catalog_hash = catalog_identity(catalog)
    card_ids = sorted(int(item["id"]) for item in catalog["cards"])
    policy = checkpoint_policy(
        checkpoint_path,
        catalog,
        catalog_sha256=catalog_hash,
        observation_profile_id=observation_profile_id,
    )
    states = synthetic_cases(
        card_ids,
        count,
        seed,
        {
            int(item["id"]): list(map(int, item.get("effects", [])))
            for item in catalog["cards"]
        },
    )
    predictions = [
        prediction
        for start in range(0, count, env_count)
        for prediction in _logits_and_choices(
            policy, states[start : start + env_count], card_ids
        )
    ]
    generator_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    header = {
        "fixture_schema": 1,
        "observation_schema_id": OBSERVATION_SCHEMA_ID,
        "observation_profile_id": policy.observation_profile_id,
        "action_schema_id": ACTION_SCHEMA_ID,
        "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        "catalog_sha256": catalog_hash,
        "generator_sha256": generator_hash,
        "decision_source": DECISION_SOURCE,
        "dev_game_count": 0,
        "synthetic_batch_size": env_count,
        "rules_validated": False,
        "seed": seed,
        "position_count": count,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(header, separators=(",", ":"), allow_nan=False) + "\n")
        for index, (state, prediction) in enumerate(
            zip(states, predictions, strict=True)
        ):
            stream.write(
                json.dumps(
                    {
                        "position_id": index,
                        "observation": state["observation"],
                        "legal_actions": state["legal_actions"],
                        "expected": prediction,
                    },
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            )
    return {
        "count": count,
        "decision_source": DECISION_SOURCE,
        "dev_game_count": 0,
        "synthetic_batch_size": env_count,
        "rules_validated": False,
        "checkpoint_policy_id": policy.policy_id,
        "observation_profile_id": policy.observation_profile_id,
        "catalog_sha256": catalog_hash,
        "generator_sha256": generator_hash,
        "output": str(output_path),
        "legal_action_min": min(len(state["legal_actions"]) for state in states),
        "legal_action_max": max(len(state["legal_actions"]) for state in states),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--observation-profile", choices=OBSERVATION_PROFILES)
    parser.add_argument(
        "--envs",
        type=int,
        default=10,
        help="reference inference batch width; no games are played",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            create_fixtures(
                checkpoint_path=args.checkpoint,
                catalog_path=args.catalog,
                output_path=args.output,
                count=args.count,
                seed=args.seed,
                env_count=args.envs,
                observation_profile_id=args.observation_profile,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
