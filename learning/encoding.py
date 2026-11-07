"""Versioned seat-view and legal-action encoding for batched learning."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

OBSERVATION_SCHEMA_ID = "witcher3-gwent-seat-view-v1"
ACTION_SCHEMA_ID = "witcher3-gwent-factorized-action-v1"
EFFECT_COUNT = 45
GLOBAL_SIZE = 64
ZONE_NAMES = (
    "own_hand",
    "own_melee",
    "own_ranged",
    "own_siege",
    "opponent_melee",
    "opponent_ranged",
    "opponent_siege",
    "own_graveyard",
    "opponent_graveyard",
    "weather",
    "own_leader",
    "opponent_leader",
    "own_melee_modifier",
    "own_ranged_modifier",
    "own_siege_modifier",
    "opponent_melee_modifier",
    "opponent_ranged_modifier",
    "opponent_siege_modifier",
)
ROWS = ("melee", "ranged", "siege")
ACTION_KINDS = (
    "pass",
    "play",
    "leader",
    "mulligan",
    "decline_mulligan",
    "choice",
)
_KIND_IDS = {kind: index for index, kind in enumerate(ACTION_KINDS)}
_PENDING_KINDS = (
    None,
    "mulligan",
    "nurse",
    "leader_resurrect",
    "leader_weather",
    "leader_discard",
    "leader_pick",
    "coin_toss",
)
_PENDING_IDS = {kind: index for index, kind in enumerate(_PENDING_KINDS)}


@dataclass(frozen=True)
class EncodedBatch:
    observations: np.ndarray
    legal_mask: np.ndarray
    action_features: np.ndarray
    action_group_ids: np.ndarray
    group_features: np.ndarray
    group_mask: np.ndarray
    action_objects: list[Sequence[dict[str, Any]]]


def observation_size(card_ids: Sequence[int]) -> int:
    vocab_size = len(card_ids)
    return 24 * vocab_size + len(ZONE_NAMES) * EFFECT_COUNT + GLOBAL_SIZE


def encode_batch(
    states: Sequence[dict[str, Any] | None], card_ids: Sequence[int]
) -> EncodedBatch:
    """Encode a native state vector into contiguous NumPy batches.

    The only game-state input is each acting seat's observation and legal
    actions. Opponent hand contents, deck order and native snapshots are not
    read.
    """
    if not card_ids or len(set(card_ids)) != len(card_ids):
        raise ValueError("card_ids must be a nonempty unique catalog vocabulary")
    card_index = {template_id: index + 1 for index, template_id in enumerate(card_ids)}
    obs_size = observation_size(card_ids)
    actions_by_state: list[list[dict[str, Any]]] = []
    groups_by_state: list[list[tuple[str, Any]]] = []
    observation_rows = np.zeros((len(states), obs_size), dtype=np.float32)

    for row_index, state in enumerate(states):
        if not state or state.get("done"):
            actions_by_state.append([])
            groups_by_state.append([])
            continue
        observation = state.get("observation")
        actions = state.get("legal_actions")
        if not isinstance(observation, dict) or not isinstance(actions, list):
            raise ValueError(
                f"state {row_index} is missing its seat view or legal actions"
            )
        observation_rows[row_index] = _encode_observation(observation, card_index)
        actions_by_state.append(actions)
        groups_by_state.append(_action_groups(actions))

    action_width = max(1, max(map(len, actions_by_state), default=0))
    group_width = max(1, max(map(len, groups_by_state), default=0))
    legal_mask = np.zeros((len(states), action_width), dtype=np.bool_)
    action_features = np.zeros((len(states), action_width, 5), dtype=np.int64)
    action_group_ids = np.full((len(states), action_width), -1, dtype=np.int64)
    group_features = np.zeros((len(states), group_width, 2), dtype=np.int64)
    group_mask = np.zeros((len(states), group_width), dtype=np.bool_)

    for batch_index, (state, actions, groups) in enumerate(
        zip(states, actions_by_state, groups_by_state, strict=True)
    ):
        if not actions:
            continue
        observation = state["observation"]
        visible_cards = _visible_instance_cards(observation)
        group_lookup = {key: index for index, key in enumerate(groups)}
        for action_index, action in enumerate(actions):
            legal_mask[batch_index, action_index] = True
            feature = _action_feature(action, visible_cards, card_index)
            action_features[batch_index, action_index] = feature
            group_index = group_lookup[_group_key(action)]
            action_group_ids[batch_index, action_index] = group_index
            if not group_mask[batch_index, group_index]:
                group_features[batch_index, group_index] = (feature[0], feature[1])
                group_mask[batch_index, group_index] = True

    return EncodedBatch(
        observations=np.ascontiguousarray(observation_rows),
        legal_mask=np.ascontiguousarray(legal_mask),
        action_features=np.ascontiguousarray(action_features),
        action_group_ids=np.ascontiguousarray(action_group_ids),
        group_features=np.ascontiguousarray(group_features),
        group_mask=np.ascontiguousarray(group_mask),
        action_objects=actions_by_state,
    )


def select_encoded_rows(encoded: EncodedBatch, rows: Sequence[int]) -> EncodedBatch:
    """Select a batch subset and trim its padded action and group dimensions."""
    row_indices = np.asarray(rows, dtype=np.intp)
    if (
        row_indices.ndim != 1
        or np.any(row_indices < 0)
        or np.any(row_indices >= len(encoded.action_objects))
    ):
        raise ValueError("encoded batch row indices are invalid")
    action_width = max(
        1, int(encoded.legal_mask[row_indices].sum(axis=1).max(initial=0))
    )
    group_width = max(
        1, int(encoded.group_mask[row_indices].sum(axis=1).max(initial=0))
    )
    return EncodedBatch(
        observations=np.ascontiguousarray(encoded.observations[row_indices]),
        legal_mask=np.ascontiguousarray(encoded.legal_mask[row_indices, :action_width]),
        action_features=np.ascontiguousarray(
            encoded.action_features[row_indices, :action_width]
        ),
        action_group_ids=np.ascontiguousarray(
            encoded.action_group_ids[row_indices, :action_width]
        ),
        group_features=np.ascontiguousarray(
            encoded.group_features[row_indices, :group_width]
        ),
        group_mask=np.ascontiguousarray(encoded.group_mask[row_indices, :group_width]),
        action_objects=[encoded.action_objects[int(row)] for row in row_indices],
    )


def _encode_observation(
    observation: dict[str, Any], card_index: dict[int, int]
) -> np.ndarray:
    vocab_size = len(card_index)
    count_start = 0
    power_start = len(ZONE_NAMES) * vocab_size
    effect_start = power_start + 6 * vocab_size
    global_start = effect_start + len(ZONE_NAMES) * EFFECT_COUNT
    encoded = np.zeros(global_start + GLOBAL_SIZE, dtype=np.float32)
    players = observation.get("players")
    if not isinstance(players, list) or len(players) != 2:
        raise ValueError("seat view must contain two player summaries")
    seat = int(observation["seat"])
    if seat not in (0, 1):
        raise ValueError("seat must be zero or one")
    by_seat = {int(player["seat"]): player for player in players}
    own = by_seat[seat]
    opponent = by_seat[1 - seat]
    _add_cards(
        encoded,
        count_start,
        effect_start,
        0,
        vocab_size,
        observation.get("hand", []),
        card_index,
    )

    for relative_seat, player in enumerate((own, opponent)):
        for row_index, row in enumerate(ROWS):
            zone = 1 + row_index if relative_seat == 0 else 4 + row_index
            row_view = player["rows"][row]
            cards = row_view.get("cards", [])
            _add_cards(
                encoded, count_start, effect_start, zone, vocab_size, cards, card_index
            )
            power_plane = power_start + (relative_seat * 3 + row_index) * vocab_size
            for card in cards:
                card_id = _template_index(card, card_index)
                current_power = card.get("score")
                if current_power is None:
                    current_power = card.get("power", 0)
                encoded[power_plane + card_id] += float(current_power) / 100.0

        grave_zone = 7 if relative_seat == 0 else 8
        _add_cards(
            encoded,
            count_start,
            effect_start,
            grave_zone,
            vocab_size,
            player.get("graveyard", []),
            card_index,
        )
        leader_zone = 10 if relative_seat == 0 else 11
        leader = player.get("leader")
        if leader is not None:
            _add_cards(
                encoded,
                count_start,
                effect_start,
                leader_zone,
                vocab_size,
                [leader],
                card_index,
            )
        modifier_base = 12 if relative_seat == 0 else 15
        for row_index, row in enumerate(ROWS):
            modifier = player.get("modifiers", {}).get(row)
            if modifier is not None:
                _add_cards(
                    encoded,
                    count_start,
                    effect_start,
                    modifier_base + row_index,
                    vocab_size,
                    [modifier],
                    card_index,
                )

    _add_cards(
        encoded,
        count_start,
        effect_start,
        9,
        vocab_size,
        observation.get("weather", []),
        card_index,
    )
    encoded[count_start:power_start] /= 10.0
    encoded[effect_start:global_start] /= 10.0

    globals_vector = encoded[global_start:]
    globals_vector[0] = float(int(observation.get("initiative", 0)) == seat)
    round_number = int(observation.get("round", 1))
    if 1 <= round_number <= 3:
        globals_vector[round_number] = 1.0
    globals_vector[5] = min(1.0, float(observation.get("rounds_played", 0)) / 3.0)
    own_score, opponent_score = _relative_pair(observation.get("scores", [0, 0]), seat)
    globals_vector[6] = np.clip(own_score / 500.0, -1.0, 1.0)
    globals_vector[7] = np.clip(opponent_score / 500.0, -1.0, 1.0)
    own_lives, opponent_lives = _relative_pair(observation.get("lives", [2, 2]), seat)
    globals_vector[8] = own_lives / 2.0
    globals_vector[9] = opponent_lives / 2.0

    round_results = observation.get("round_results", [])[-3:]
    for index, result in enumerate(round_results):
        offset = 10 + index * 6
        scores = result.get("scores", [0, 0])
        own_round, opponent_round = _relative_pair(scores, seat)
        globals_vector[offset] = np.clip(own_round / 500.0, -1.0, 1.0)
        globals_vector[offset + 1] = np.clip(opponent_round / 500.0, -1.0, 1.0)
        winner = result.get("winner")
        if winner is None:
            globals_vector[offset + 3] = 1.0
        elif int(winner) == seat:
            globals_vector[offset + 2] = 1.0
        else:
            globals_vector[offset + 4] = 1.0
        globals_vector[offset + 5] = 1.0

    for relative_seat, player in enumerate((own, opponent)):
        offset = 28 + relative_seat * 11
        faction = int(player.get("faction", 0))
        if 0 <= faction < 6:
            globals_vector[offset + faction] = 1.0
        globals_vector[offset + 6] = min(1.0, float(player.get("hand_count", 0)) / 20.0)
        globals_vector[offset + 7] = min(
            1.0, float(player.get("deck_count", 0)) / 100.0
        )
        globals_vector[offset + 8] = float(bool(player.get("passed", False)))
        globals_vector[offset + 9] = float(bool(player.get("leader_used", False)))
        globals_vector[offset + 10] = float(player.get("leader") is not None)

    pending = observation.get("pending_choice")
    pending_kind = pending.get("kind") if isinstance(pending, dict) else None
    pending_index = _PENDING_IDS.get(pending_kind)
    if pending_index is None:
        pending_index = 0
    globals_vector[50 + pending_index] = 1.0
    if isinstance(pending, dict):
        pending_seat = pending.get("seat")
        graveyard_seat = pending.get("graveyard_seat")
        globals_vector[58] = _relative_seat(pending_seat, seat)
        globals_vector[59] = _relative_seat(graveyard_seat, seat)
        globals_vector[60] = min(1.0, float(pending.get("remaining", 0)) / 2.0)
        globals_vector[61] = min(1.0, len(pending.get("templates", [])) / 10.0)
    globals_vector[62] = float(bool(observation.get("terminal", False)))
    globals_vector[63] = min(1.0, float(observation.get("decision_id", 0)) / 10_000.0)
    return encoded


def _add_cards(
    encoded: np.ndarray,
    count_start: int,
    effect_start: int,
    zone: int,
    vocab_size: int,
    cards: Sequence[dict[str, Any]],
    card_index: dict[int, int],
) -> None:
    if not isinstance(cards, list):
        raise ValueError("card zone must be a list")
    count_plane = count_start + zone * vocab_size
    effect_plane = effect_start + zone * EFFECT_COUNT
    for card in cards:
        index = _template_index(card, card_index)
        encoded[count_plane + index] += 1.0
        for effect in card.get("effects", []):
            effect = int(effect)
            if not 0 <= effect < EFFECT_COUNT:
                raise ValueError(f"effect id {effect} is outside observation schema v1")
            encoded[effect_plane + effect] += 1.0


def _template_index(card: dict[str, Any], card_index: dict[int, int]) -> int:
    template_id = int(card["template_id"])
    if template_id not in card_index:
        raise ValueError(f"template {template_id} is not in the frozen card vocabulary")
    return card_index[template_id] - 1


def _relative_pair(values: Sequence[Any], seat: int) -> tuple[float, float]:
    if len(values) != 2:
        raise ValueError("expected a two-seat value")
    return float(values[seat]), float(values[1 - seat])


def _relative_seat(value: Any, own_seat: int) -> float:
    if value is None:
        return 0.0
    return 1.0 if int(value) == own_seat else -1.0


def _visible_instance_cards(observation: dict[str, Any]) -> dict[int, int]:
    cards: dict[int, int] = {}
    zones = [observation.get("hand", []), observation.get("weather", [])]
    for player in observation.get("players", []):
        zones.append(player.get("graveyard", []))
        if player.get("leader") is not None:
            zones.append([player["leader"]])
        zones.extend(
            player.get("rows", {}).get(row, {}).get("cards", []) for row in ROWS
        )
        zones.extend(
            [modifier]
            for modifier in player.get("modifiers", {}).values()
            if modifier is not None
        )
    for zone in zones:
        for card in zone:
            if card.get("instance_id") is not None:
                cards[int(card["instance_id"])] = int(card["template_id"])
    return cards


def _action_feature(
    action: dict[str, Any],
    visible_cards: dict[int, int],
    card_index: dict[int, int],
) -> tuple[int, int, int, int, int]:
    kind = str(action.get("kind"))
    if kind not in _KIND_IDS:
        raise ValueError(f"unknown legal action kind {kind!r}")
    instance = action.get("instance_id")
    target = action.get("target_instance_id")
    template = action.get("template_id")
    card_template = (
        int(template)
        if template is not None
        else visible_cards.get(int(instance))
        if instance is not None
        else None
    )
    target_template = visible_cards.get(int(target)) if target is not None else None
    card_feature = card_index.get(card_template, 0)
    target_feature = card_index.get(target_template, 0)
    template_feature = card_index.get(int(template), 0) if template is not None else 0
    row = action.get("row")
    row_feature = ROWS.index(row) + 1 if row in ROWS else 0
    return (
        _KIND_IDS[kind],
        card_feature,
        target_feature,
        row_feature,
        template_feature,
    )


def _action_groups(actions: Sequence[dict[str, Any]]) -> list[tuple[str, Any]]:
    keys = dict.fromkeys(_group_key(action) for action in actions)
    return list(keys)


def _group_key(action: dict[str, Any]) -> tuple[str, Any]:
    kind = str(action.get("kind"))
    if kind == "choice":
        return (kind, None)
    if kind in ("play", "leader", "mulligan"):
        return (kind, action.get("instance_id"))
    if kind == "pass" or kind == "decline_mulligan":
        return (kind, None)
    return (kind, action.get("template_id", action.get("instance_id")))


def random_legal_action(
    actions: Sequence[dict[str, Any]], rng: random.Random
) -> dict[str, Any]:
    """Sample a primary choice uniformly, then its legal placement/target."""
    if not actions:
        raise ValueError("random policy needs at least one legal action")
    groups: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for action in actions:
        groups.setdefault(_group_key(action), []).append(action)
    options = rng.choice(list(groups.values()))
    return rng.choice(options)
