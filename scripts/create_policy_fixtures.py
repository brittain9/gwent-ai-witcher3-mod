#!/usr/bin/env python3
"""Deterministic synthetic schema coverage, without an engine or game records.

The action lists are schema-shaped test inputs. They do not assert native-game
legality, gameplay strength, self-play coverage or rules parity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from learning.catalog_identity import catalog_identity
from learning.encoding import ACTION_SCHEMA_ID, OBSERVATION_SCHEMA_ID, encode_batch
from learning.policy import checkpoint_policy, served_argmax

DECISION_SOURCE = "synthetic schema coverage (not gameplay or rules parity)"
DEFAULT_CHECKPOINT = PROJECT_ROOT / ".runtime/learner-pilot-v5-checkpoint.pt"
DEFAULT_CATALOG = PROJECT_ROOT / ".runtime/catalog.json"
DEFAULT_OUTPUT = PROJECT_ROOT / ".runtime/policy-as3-fixtures/synthetic.jsonl"
ROWS = ("melee", "ranged", "siege")
PENDING_KINDS = (
    None,
    "mulligan",
    "nurse",
    "leader_resurrect",
    "leader_weather",
    "leader_discard",
    "leader_pick",
    "coin_toss",
)


def synthetic_cases(
    card_ids: list[int],
    count: int,
    seed: int,
    effects_by_id: dict[int, list[int]] | None = None,
) -> list[dict[str, Any]]:
    """Construct seat-limited encoder inputs; do not simulate game rules."""
    if not card_ids or len(set(card_ids)) != len(card_ids):
        raise ValueError("synthetic cases require a nonempty unique vocabulary")
    rng = random.Random(seed)
    effects_by_id = {} if effects_by_id is None else effects_by_id
    cases = []
    for index in range(count):
        next_instance = index * 1000

        def card() -> dict[str, Any]:
            nonlocal next_instance
            next_instance += 1
            power = rng.randrange(21)
            template_id = card_ids[
                (index + next_instance + rng.randrange(len(card_ids))) % len(card_ids)
            ]
            return {
                "instance_id": next_instance,
                "template_id": template_id,
                "power": power,
                "score": max(0, power + rng.randrange(-3, 5)),
                "effects": list(effects_by_id.get(template_id, [])),
            }

        seat = (index + index // len(PENDING_KINDS)) % 2
        hand = [card() for _ in range(1 + index % 6)]
        players = []
        for player_seat in (0, 1):
            players.append(
                {
                    "seat": player_seat,
                    "faction": (index // 2 + player_seat) % 6,
                    "hand_count": len(hand)
                    if player_seat == seat
                    else (index * 7) % 15,
                    "deck_count": (index * 3 + player_seat) % 31,
                    "passed": (index // 3 + player_seat) % 2 == 0,
                    "leader_used": (index + player_seat) % 3 == 0,
                    "leader": None if (index + player_seat) % 4 == 0 else card(),
                    "rows": {
                        row: {
                            "cards": [
                                card()
                                for _ in range((index + player_seat + row_index) % 4)
                            ]
                        }
                        for row_index, row in enumerate(ROWS)
                    },
                    "graveyard": [card() for _ in range((index + player_seat) % 3)],
                    "modifiers": {
                        row: None
                        if (index + player_seat + row_index) % 3 == 0
                        else card()
                        for row_index, row in enumerate(ROWS)
                    },
                }
            )
        round_number = index % 3 + 1
        round_results = [
            {
                "scores": [rng.randrange(101), rng.randrange(101)],
                "winner": (None, 0, 1)[(index + prior) % 3],
            }
            for prior in range(round_number - 1)
        ]
        pending_kind = PENDING_KINDS[index % len(PENDING_KINDS)]
        pending = (
            None
            if pending_kind is None
            else {
                "kind": pending_kind,
                "seat": seat,
                "graveyard_seat": (index // 2) % 2,
                "remaining": index % 2 + 1,
                "templates": [
                    card_ids[(index + offset) % len(card_ids)]
                    for offset in range(index % 4)
                ],
            }
        )
        observation = {
            "seat": seat,
            "initiative": (index // 2) % 2,
            "decision_id": index,
            "round": round_number,
            "rounds_played": len(round_results),
            "scores": [rng.randrange(201), rng.randrange(201)],
            "lives": [1 + index % 2, 1 + (index // 2) % 2],
            "round_results": round_results,
            "hand": hand,
            "players": players,
            "weather": [card() for _ in range(index % 3)],
            "pending_choice": pending,
            "terminal": False,
        }
        cases.append(
            {
                "observation": observation,
                "legal_actions": _synthetic_actions(observation, index, card_ids),
                "done": False,
            }
        )
    return cases


def _synthetic_actions(
    observation: dict[str, Any], index: int, card_ids: list[int]
) -> list[dict[str, Any]]:
    hand = observation["hand"]
    pending = observation["pending_choice"]
    kind = None if pending is None else pending["kind"]
    if kind == "mulligan":
        return [
            {
                "id": f"mulligan:{item['instance_id']}",
                "kind": "mulligan",
                "instance_id": item["instance_id"],
            }
            for item in hand
        ] + [{"id": "decline_mulligan", "kind": "decline_mulligan"}]
    if kind in {"nurse", "leader_resurrect", "leader_discard"}:
        visible = hand + [
            item for player in observation["players"] for item in player["graveyard"]
        ]
        return [
            {
                "id": f"choice:{item['instance_id']}",
                "kind": "choice",
                "instance_id": item["instance_id"],
            }
            for item in visible
        ]
    if kind in {"leader_weather", "leader_pick"}:
        templates = dict.fromkeys(
            card_ids[(index + offset) % len(card_ids)]
            for offset in range(1 + index % 4)
        )
        return [
            {
                "id": f"choice:template:{template}",
                "kind": "choice",
                "template_id": template,
            }
            for template in templates
        ]
    if kind == "coin_toss":
        return [{"id": f"choice:seat:{seat}", "kind": "choice"} for seat in (0, 1)]
    actions = [{"id": "pass", "kind": "pass"}]
    if index // len(PENDING_KINDS) % 3 == 0:
        return actions
    for item in hand[:3]:
        actions.extend(
            {
                "id": f"play:{item['instance_id']}:{row}",
                "kind": "play",
                "instance_id": item["instance_id"],
                "row": row,
            }
            for row in ROWS
        )
    own = observation["players"][observation["seat"]]
    target = next((item for row in ROWS for item in own["rows"][row]["cards"]), None)
    if target is not None:
        actions.append(
            {
                "id": f"play:{hand[0]['instance_id']}:target:{target['instance_id']}",
                "kind": "play",
                "instance_id": hand[0]["instance_id"],
                "target_instance_id": target["instance_id"],
            }
        )
    if own["leader"] is not None and not own["leader_used"]:
        actions.append(
            {
                "id": f"leader:{own['leader']['instance_id']}",
                "kind": "leader",
                "instance_id": own["leader"]["instance_id"],
            }
        )
    return actions


def _logits_and_choices(
    policy: Any, states: list[dict[str, Any]], card_ids: list[int]
) -> list[dict[str, Any]]:
    encoded = encode_batch(states, card_ids)
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
    policy = checkpoint_policy(checkpoint_path, catalog, catalog_sha256=catalog_hash)
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
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
