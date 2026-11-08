"""Pure owned-RNG difficulty selection; inference and native commits stay external.

Temperature zero is served group-first. Positive temperature samples the group
and then its conditional action, consuming exactly two owned uniforms. No
training target, native difficulty calibration or player latency is implied.
"""

from __future__ import annotations

import math


class OwnedActionRng:
    """Explicit xorshift32 state, separate from effect/planning generators."""

    def __init__(self, seed):
        if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
            raise ValueError("owned action seed must be uint32")
        self.seed = seed
        self.state = seed or 0x6D2B79F5
        self.cursor = 0

    def random(self):
        value = self.state
        value ^= (value << 13) & 0xFFFFFFFF
        value ^= value >> 17
        value ^= (value << 5) & 0xFFFFFFFF
        self.state = value & 0xFFFFFFFF
        self.cursor += 1
        return self.state / 4294967296


def _finite(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("finite numeric " + label + " required")
    return value


def _ordered_sum(values):
    # Python's built-in sum can use compensated accumulation; the AS3 loop does not.
    total = 0.0
    for value in values:
        total += value
    return total


def _softmax(logits, members, temperature):
    maximum = max(logits[index] for index in members)
    weights = [math.exp((logits[index] - maximum) / temperature) for index in members]
    # Preserve the authored AS3 raw-weight accumulation before normalization.
    total = _ordered_sum(weights)
    return weights, [weight / total for weight in weights]


def _sample(probabilities, members, uniform):
    _finite(uniform, "owned uniform")
    if not 0 <= uniform < 1:
        raise ValueError("owned uniform must be in [0,1)")
    # Skip zero support explicitly, including uniform0 after exponential underflow.
    total = _ordered_sum(probabilities)
    draw = uniform * total
    last = None
    for probability, member in zip(probabilities, members, strict=True):
        if probability <= 0:
            continue
        last = member
        draw -= probability
        if draw < 0:
            return member
    if last is None:
        raise ValueError("empty probability support")
    return last


def select_temperature(
    group_logits, local_logits, action_group_ids, *, temperature, rng
):
    """Return actual normalized law/choice in unchanged canonical action order."""
    _finite(temperature, "temperature")
    if temperature < 0:
        raise ValueError("temperature cannot be negative")
    if (
        not isinstance(group_logits, list)
        or not group_logits
        or not isinstance(local_logits, list)
        or not local_logits
    ):
        raise ValueError("nonempty group/local logit vectors required")
    groups = [_finite(value, "group logit") for value in group_logits]
    local = [_finite(value, "local logit") for value in local_logits]
    if not isinstance(action_group_ids, list) or len(action_group_ids) != len(local):
        raise ValueError("canonical group map differs from local logits")
    if any(
        type(group) is not int or not 0 <= group < len(groups)
        for group in action_group_ids
    ):
        raise ValueError("canonical action group is out of range")
    members = [
        [i for i, group in enumerate(action_group_ids) if group == g]
        for g in range(len(groups))
    ]
    if any(not row for row in members):
        raise ValueError("every group must contain a canonical action")
    draws = []
    conditionals = [[0.0] * len(local) for _ in groups]
    if temperature == 0:
        chosen_group = max(range(len(groups)), key=lambda g: groups[g])
        group_probabilities = [float(g == chosen_group) for g in range(len(groups))]
        for group, row in enumerate(members):
            chosen = max(row, key=lambda i: local[i])
            conditionals[group][chosen] = 1.0
        chosen_action = max(members[chosen_group], key=lambda i: local[i])
    else:
        if not callable(getattr(rng, "random", None)):
            raise ValueError("positive temperature requires explicit owned RNG")
        group_weights, group_probabilities = _softmax(
            groups, list(range(len(groups))), temperature
        )
        local_weights = []
        for group, row in enumerate(members):
            weights, probabilities = _softmax(local, row, temperature)
            local_weights.append(weights)
            for action, probability in zip(row, probabilities, strict=True):
                conditionals[group][action] = probability
        draws.append(rng.random())
        chosen_group = _sample(group_weights, list(range(len(groups))), draws[0])
        draws.append(rng.random())
        chosen_action = _sample(
            local_weights[chosen_group],
            members[chosen_group],
            draws[1],
        )
    joint = [
        group_probabilities[group] * conditionals[group][i]
        for i, group in enumerate(action_group_ids)
    ]
    probability = joint[chosen_action]
    if temperature == 0:
        chosen_log_probability = 0.0
    else:
        local_index = members[chosen_group].index(chosen_action)
        chosen_log_probability = (
            math.log(group_weights[chosen_group])
            - math.log(_ordered_sum(group_weights))
            + math.log(local_weights[chosen_group][local_index])
            - math.log(_ordered_sum(local_weights[chosen_group]))
        )
    return {
        "selection_rule": "served-group-first-v1"
        if temperature == 0
        else "owned-temperature-group-local-v1",
        "temperature": temperature,
        "chosen_group": chosen_group,
        "chosen_action": chosen_action,
        "group_probabilities": group_probabilities,
        "conditional_probabilities": conditionals,
        "joint_probabilities": joint,
        "chosen_probability": probability,
        "chosen_log_probability": chosen_log_probability,
        "law_scope": "action-selection-at-declared-temperature-not-neural-prior",
        "draws_consumed": len(draws),
        "action_rng_draws": draws,
    }
