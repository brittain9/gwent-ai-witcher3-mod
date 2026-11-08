"""Policy-only evaluation adapter for a frozen Ataraxos checkpoint."""

from __future__ import annotations

import hashlib
import math
import random
from pathlib import Path
from typing import Any, Sequence

import torch

from scripts.policy_format import (
    FULL_FACTION_PROFILE,
    checkpoint_observation_profile,
    validate_observation_profile,
)

from .catalog_identity import catalog_identity
from .encoding import (
    ACTION_SCHEMA_ID,
    OBSERVATION_SCHEMA_ID,
    EncodedBatch,
    encode_batch,
    observation_size,
)
from .model import AtaraxosPolicyValue, PolicyDistribution

ENSEMBLE_PREFIX = "ensemble:"
# The policy-ID suffix of a checkpoint played by the served rule. An ID without it names
# the joint rule, which every read before 2026-10-31 used.
SERVED_ARGMAX_SUFFIX = "+served-argmax"


def served_argmax(
    group_logits: Sequence[float],
    local_logits: Sequence[float],
    action_group_ids: Sequence[int],
    group_features: Sequence[Sequence[int]],
    action_features: Sequence[Sequence[int]],
) -> int:
    """Return the legal-action index the mod plays at temperature 0 (`GwaiNetPolicy.as` `evaluate`).

    1. The group with the highest group logit; the lowest group index on a tie.
    2. Inside that group, the action with the highest local logit; the lowest legal index on a tie.

    A group logit depends only on the trunk and the group's features, and a local logit only on
    the trunk and the action's features. So groups (or actions) with identical features tie in
    exact arithmetic, and the AS3 and the Rust engine, which compute such rows identically, keep
    the first. Torch's batched products can round identical rows apart by an ulp, so each step
    returns the first index whose features equal the winner's.
    """
    if not local_logits or not len(local_logits) == len(action_group_ids) == len(
        action_features
    ):
        raise ValueError(
            "served argmax needs one local logit, group ID and feature row per action"
        )
    if len(group_logits) != len(group_features) or not group_logits:
        raise ValueError("served argmax needs one logit and one feature row per group")
    best_group = max(range(len(group_logits)), key=group_logits.__getitem__)
    best_group = next(
        index
        for index in range(len(group_features))
        if list(group_features[index]) == list(group_features[best_group])
    )
    candidates = [
        index for index, group in enumerate(action_group_ids) if group == best_group
    ]
    if not candidates:
        raise ValueError("served argmax selected a group without a legal action")
    best_action = max(candidates, key=local_logits.__getitem__)
    return next(
        index
        for index in candidates
        if list(action_features[index]) == list(action_features[best_action])
    )


class ProbabilityPolicy:
    """Temperature-zero play over `probabilities_many`: the joint argmax, first legal action on a tie."""

    policy_id: str

    def probabilities_many(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> list[list[float]]:
        raise NotImplementedError

    def log_probs(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> list[list[float]]:
        """Return masked log probabilities in each input legal-action order."""
        probabilities = self.probabilities_many(observations, legal_lists)
        return [
            [
                math.log(probability) if probability > 0 else -math.inf
                for probability in row
            ]
            for row in probabilities
        ]

    def joint_act_many(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Choose the legal action with the highest joint probability (group times conditional).

        The rule of every read before 2026-10-31 and of the search rollouts (the engine's
        `first_argmax` over the same probabilities); the mod plays `served_argmax` instead.
        """
        log_probs = self.log_probs(observations, legal_lists)
        return [
            actions[max(range(len(actions)), key=log_probs[index].__getitem__)]
            for index, actions in enumerate(legal_lists)
        ]

    def __call__(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        _rng: random.Random,
    ) -> dict[str, Any]:
        return self.joint_act_many([observation], [legal_actions])[0]


class BatchedCheckpointPolicy(ProbabilityPolicy):
    """CPU inference adapter with a shared encoder and one forward per batch."""

    def __init__(
        self,
        model: AtaraxosPolicyValue,
        card_ids: list[int],
        policy_id: str,
        *,
        device: torch.device = torch.device("cpu"),
        checkpoint_sha256: str | None = None,
        observation_profile_id: str = FULL_FACTION_PROFILE,
    ) -> None:
        self._model = model
        self._card_ids = card_ids
        self.policy_id = policy_id
        self._device = device
        self.checkpoint_sha256 = checkpoint_sha256
        self.observation_profile_id = validate_observation_profile(
            observation_profile_id
        )

    def _encode(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> Any:
        """Validate the batch and encode it; None for an empty batch."""
        if len(observations) != len(legal_lists):
            raise ValueError("observations and legal_lists must have the same length")
        if any(not actions for actions in legal_lists):
            raise ValueError(
                "policy inference requires at least one legal action per state"
            )
        if not observations:
            return None
        return encode_batch(
            [
                {"done": False, "observation": observation, "legal_actions": actions}
                for observation, actions in zip(observations, legal_lists, strict=True)
            ],
            self._card_ids,
            observation_profile_id=self.observation_profile_id,
        )

    def _forward(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> tuple[Any, torch.Tensor, torch.Tensor] | None:
        """Validate the batch and run one forward; None for an empty batch."""
        encoded = self._encode(observations, legal_lists)
        if encoded is None:
            return None
        return self.forward_encoded(encoded)

    def forward_encoded(
        self, encoded: EncodedBatch
    ) -> tuple[PolicyDistribution, torch.Tensor, torch.Tensor]:
        """One forward over rows already encoded with this policy's card vocabulary.

        The trainer's frozen opponent seat passes the rows of its rollout batch.
        """
        with torch.inference_mode():
            return self._model(
                torch.as_tensor(encoded.observations, device=self._device),
                torch.as_tensor(encoded.action_features, device=self._device),
                torch.as_tensor(encoded.action_group_ids, device=self._device),
                torch.as_tensor(encoded.legal_mask, device=self._device),
                torch.as_tensor(encoded.group_features, device=self._device),
                torch.as_tensor(encoded.group_mask, device=self._device),
            )

    def probabilities_many(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> list[list[float]]:
        """Return masked probabilities in each input legal-action order."""
        outputs = self._forward(observations, legal_lists)
        if outputs is None:
            return []
        distribution = outputs[0]
        return [
            distribution.joint_probabilities[index, : len(actions)].cpu().tolist()
            for index, actions in enumerate(legal_lists)
        ]

    def values_many(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> list[tuple[float, list[float]]]:
        """Return the value head per state: ``(V, [loss, draw, win])``.

        V is the acting seat's win probability plus half the draw probability,
        the number the native engine reports as ``value`` and ``network_value``.
        """
        outputs = self._forward(observations, legal_lists)
        if outputs is None:
            return []
        _, values, outcome_probabilities = outputs
        return [
            (float(value), probabilities)
            for value, probabilities in zip(
                values.cpu().tolist(), outcome_probabilities.cpu().tolist(), strict=True
            )
        ]

    def served_act_many(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Choose each state's action by the mod's temperature-zero rule (`served_argmax`)."""
        encoded = self._encode(observations, legal_lists)
        if encoded is None:
            return []
        return [
            actions[index]
            for actions, index in zip(
                legal_lists, self.served_indices(encoded), strict=True
            )
        ]

    def served_indices(self, encoded: EncodedBatch) -> list[int]:
        """The served rule's legal-action index for each encoded row (its legal actions come first)."""
        with torch.inference_mode():
            _, group_logits, local_logits = self._model.logits(
                torch.as_tensor(encoded.observations, device=self._device),
                torch.as_tensor(encoded.action_features, device=self._device),
                torch.as_tensor(encoded.group_features, device=self._device),
            )
        group_rows, local_rows = (
            group_logits.cpu().tolist(),
            local_logits.cpu().tolist(),
        )
        chosen = []
        for index in range(len(group_rows)):
            action_count = int(encoded.legal_mask[index].sum())
            group_count = int(encoded.group_mask[index].sum())
            chosen.append(
                served_argmax(
                    group_rows[index][:group_count],
                    local_rows[index][:action_count],
                    encoded.action_group_ids[index, :action_count].tolist(),
                    encoded.group_features[index, :group_count].tolist(),
                    encoded.action_features[index, :action_count].tolist(),
                )
            )
        return chosen


class ServedArgmaxPolicy:
    """A checkpoint played by the mod's temperature-zero rule: the best group, then its best action.

    `checkpoint:` reads play this form, so a read measures what the mod serves. The policy ID
    is the checkpoint's with `SERVED_ARGMAX_SUFFIX`; the bare checkpoint policy keeps the joint
    rule and its unsuffixed ID, so the reads made before this rule remain reproducible.
    """

    def __init__(self, network: BatchedCheckpointPolicy) -> None:
        self.network = network
        self.policy_id = network.policy_id + SERVED_ARGMAX_SUFFIX
        self.checkpoint_sha256 = network.checkpoint_sha256

    def log_probs(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> list[list[float]]:
        """The network's joint log probabilities (traces rank `top_actions` by them)."""
        return self.network.log_probs(observations, legal_lists)

    def __call__(
        self,
        observation: dict[str, Any],
        legal_actions: list[dict[str, Any]],
        _rng: random.Random,
    ) -> dict[str, Any]:
        return self.network.served_act_many([observation], [legal_actions])[0]


def argmax_rule(agent_id: str) -> str:
    """The temperature-zero rule a record's agent build ID names: ``served`` or ``joint``.

    The rule is part of the policy, before ``@``: `SERVED_ARGMAX_SUFFIX` marks the served
    rule, and an ID without it is the joint rule (every read before 2026-10-31).
    """
    return "served" if SERVED_ARGMAX_SUFFIX in agent_id.split("@", 1)[0] else "joint"


def read_agent_id(records: list[dict[str, Any]]) -> str:
    """The one agent build ID of a read's records; a read with none or several is refused."""
    agents = {str(record.get("build_ids", {}).get("agent", "")) for record in records}
    if len(agents) != 1 or not next(iter(agents)):
        raise ValueError(
            f"a read's records must name one agent build, not {sorted(agents)}"
        )
    return agents.pop()


def checkpoint_player(network: BatchedCheckpointPolicy, agent_id: str) -> Any:
    """The checkpoint as a record's agent played it: by the rule its build ID names.

    A replay of a recorded game must play the rule the read played, or it splits from the
    record on the decisions where the served and joint rules differ.
    """
    if (
        network.checkpoint_sha256 is not None
        and network.checkpoint_sha256[:12] not in agent_id
    ):
        raise ValueError(
            f"the record's agent {agent_id!r} is not checkpoint {network.checkpoint_sha256[:12]}"
        )
    return ServedArgmaxPolicy(network) if argmax_rule(agent_id) == "served" else network


class EnsemblePolicy(ProbabilityPolicy):
    """Equal-weight mixture of checkpoint policies.

    Each member's factorized policy gives a joint probability per legal action; the
    ensemble's probability is their arithmetic mean, so play is the argmax of the mean
    (the joint rule: the mod serves one checkpoint, so an ensemble has no served form).
    """

    def __init__(self, members: list[BatchedCheckpointPolicy]) -> None:
        if not members:
            raise ValueError("an ensemble needs at least one member")
        if any(member.checkpoint_sha256 is None for member in members):
            raise ValueError("every ensemble member needs its checkpoint SHA-256")
        self.members = members
        self.member_sha256s = [str(member.checkpoint_sha256) for member in members]
        self.policy_id = ENSEMBLE_PREFIX + ",".join(
            sha[:12] for sha in self.member_sha256s
        )
        self.identity_sha256 = hashlib.sha256(
            ",".join(self.member_sha256s).encode("ascii")
        ).hexdigest()

    def probabilities_many(
        self,
        observations: list[dict[str, Any]],
        legal_lists: list[list[dict[str, Any]]],
    ) -> list[list[float]]:
        """Return the members' mean joint probability in each input legal-action order."""
        rows = [
            member.probabilities_many(observations, legal_lists)
            for member in self.members
        ]
        count = len(self.members)
        return [
            [math.fsum(values) / count for values in zip(*state_rows, strict=True)]
            for state_rows in zip(*rows, strict=True)
        ]


def parse_ensemble_spec(spec: str) -> list[Path]:
    """`ensemble:PATH1,PATH2,...` -> member paths in the given order (repeats allowed)."""
    if not spec.startswith(ENSEMBLE_PREFIX):
        raise ValueError(f"an ensemble spec starts with {ENSEMBLE_PREFIX!r}")
    values = spec[len(ENSEMBLE_PREFIX) :].split(",")
    if any(not value for value in values):
        raise ValueError("ensemble: needs one or more comma-separated checkpoint paths")
    return [Path(value) for value in values]


def ensemble_policy(
    checkpoint_paths: list[Path],
    catalog: dict[str, Any],
    *,
    catalog_sha256: str,
    device: torch.device = torch.device("cpu"),
) -> EnsemblePolicy:
    return EnsemblePolicy(
        [
            checkpoint_policy(
                path, catalog, catalog_sha256=catalog_sha256, device=device
            )
            for path in checkpoint_paths
        ]
    )


def checkpoint_policy(
    checkpoint_path: Path,
    catalog: dict[str, Any],
    *,
    catalog_sha256: str,
    device: torch.device = torch.device("cpu"),
    observation_profile_id: str | None = None,
) -> BatchedCheckpointPolicy:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model_config = checkpoint.get("model_config", {})
    identity = checkpoint.get("identity", {})
    checkpoint_hash = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    profile = checkpoint_observation_profile(
        checkpoint_hash, model_config, observation_profile_id
    )
    card_ids = sorted(int(card["id"]) for card in catalog["cards"])
    expected_catalog = catalog_identity(catalog)
    if (
        identity.get("catalog_sha256") != expected_catalog
        or catalog_sha256 != expected_catalog
    ):
        raise ValueError("checkpoint catalog hash does not match the loaded catalog")
    if identity.get("card_ids") != card_ids:
        raise ValueError("checkpoint card vocabulary does not match the loaded catalog")
    if model_config.get("observation_schema_id") != OBSERVATION_SCHEMA_ID:
        raise ValueError("checkpoint uses an unsupported observation schema")
    if model_config.get("action_schema_id") != ACTION_SCHEMA_ID:
        raise ValueError("checkpoint uses an unsupported action schema")
    expected_size = observation_size(card_ids)
    if model_config.get("observation_size") != expected_size:
        raise ValueError("checkpoint observation size does not match its schema")
    model = AtaraxosPolicyValue(
        expected_size,
        len(card_ids),
        width=int(model_config["width"]),
        blocks=int(model_config["blocks"]),
    )
    model.load_state_dict(checkpoint["policy_state"], strict=True)
    model.to(device)
    model.eval()

    policy_id = checkpoint_policy_id(checkpoint, checkpoint_hash)
    if profile != FULL_FACTION_PROFILE:
        policy_id += f"+observation-profile:{profile}"
    return BatchedCheckpointPolicy(
        model,
        card_ids,
        policy_id,
        device=device,
        checkpoint_sha256=checkpoint_hash,
        observation_profile_id=profile,
    )


def checkpoint_policy_id(checkpoint: dict[str, Any], checkpoint_sha256: str) -> str:
    """The policy ID every runner records for a checkpoint: run ID and file hash prefix."""
    identity = checkpoint.get("identity", {})
    return (
        f"ataraxos-policy:{identity.get('run_id', 'unknown')}:{checkpoint_sha256[:12]}"
    )
