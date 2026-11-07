"""Deterministic fictional seat views for encoder and inference checks."""

from __future__ import annotations

import random
from typing import Any

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
