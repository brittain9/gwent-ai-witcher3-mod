"""Masked policy/value model with a card-choice and placement factorization."""

from __future__ import annotations

from typing import NamedTuple

import torch
from torch import nn
from torch.nn import functional as F


class PolicyDistribution(NamedTuple):
    joint_probabilities: torch.Tensor
    joint_log_probabilities: torch.Tensor
    group_probabilities: torch.Tensor
    group_log_probabilities: torch.Tensor
    conditional_probabilities: torch.Tensor
    conditional_log_probabilities: torch.Tensor
    entropy: torch.Tensor


def masked_softmax(
    logits: torch.Tensor, mask: torch.Tensor, dim: int = -1
) -> torch.Tensor:
    """Softmax over true entries; illegal and padded entries receive zero mass."""
    if logits.shape != mask.shape or mask.dtype is not torch.bool:
        raise ValueError("logits and boolean mask must have the same shape")
    if logits.size(dim) == 0:
        raise ValueError("masked softmax needs a nonempty action dimension")
    has_legal = mask.any(dim=dim, keepdim=True)
    safe_mask = mask.clone()
    first = [slice(None)] * mask.ndim
    first[dim] = 0
    safe_mask[tuple(first)] |= ~has_legal.squeeze(dim)
    safe_logits = logits.masked_fill(~safe_mask, -torch.inf)
    probabilities = torch.softmax(safe_logits, dim=dim)
    probabilities = torch.where(mask, probabilities, torch.zeros_like(probabilities))
    return torch.where(has_legal, probabilities, torch.zeros_like(probabilities))


def _safe_log(probabilities: torch.Tensor) -> torch.Tensor:
    tiny = torch.finfo(probabilities.dtype).tiny
    return torch.where(
        probabilities > 0,
        probabilities.clamp_min(tiny).log(),
        torch.zeros_like(probabilities),
    )


def factorized_distribution(
    group_logits: torch.Tensor,
    local_logits: torch.Tensor,
    action_group_ids: torch.Tensor,
    legal_mask: torch.Tensor,
    group_mask: torch.Tensor,
) -> PolicyDistribution:
    """Build a hierarchical distribution whose primary mass ignores option count."""
    if group_logits.shape != group_mask.shape:
        raise ValueError("group logits and group mask must align")
    if (
        local_logits.shape != legal_mask.shape
        or action_group_ids.shape != legal_mask.shape
    ):
        raise ValueError("local logits, action group ids and legal mask must align")
    if group_logits.shape[0] != local_logits.shape[0]:
        raise ValueError("group and action batches must align")
    if legal_mask.dtype is not torch.bool or group_mask.dtype is not torch.bool:
        raise ValueError("legal and group masks must be boolean")
    if torch.any(
        legal_mask
        & ((action_group_ids < 0) | (action_group_ids >= group_logits.size(1)))
    ):
        raise ValueError("every legal action must reference a valid group")

    group_probabilities = masked_softmax(group_logits, group_mask)
    group_log_probabilities = _safe_log(group_probabilities)
    groups = torch.arange(group_logits.size(1), device=local_logits.device)
    action_belongs = action_group_ids[:, None, :] == groups[None, :, None]
    local_masks = action_belongs & legal_mask[:, None, :]
    local_logits_by_group = local_logits[:, None, :].expand_as(local_masks)
    conditional_probabilities = masked_softmax(local_logits_by_group, local_masks)
    conditional_log_probabilities = _safe_log(conditional_probabilities)

    safe_group_ids = action_group_ids.clamp(min=0, max=max(0, group_logits.size(1) - 1))
    selected_local = conditional_probabilities.gather(
        1, safe_group_ids[:, None, :].expand(-1, 1, -1)
    ).squeeze(1)
    selected_local_logp = conditional_log_probabilities.gather(
        1, safe_group_ids[:, None, :].expand(-1, 1, -1)
    ).squeeze(1)
    selected_group_logp = group_log_probabilities.gather(1, safe_group_ids)
    joint_probabilities = group_probabilities.gather(1, safe_group_ids) * selected_local
    joint_probabilities = torch.where(
        legal_mask, joint_probabilities, torch.zeros_like(joint_probabilities)
    )
    joint_log_probabilities = selected_group_logp + selected_local_logp
    joint_log_probabilities = torch.where(
        legal_mask, joint_log_probabilities, torch.zeros_like(joint_log_probabilities)
    )

    group_entropy = -(group_probabilities * group_log_probabilities).sum(dim=1)
    local_entropy_by_group = -(
        conditional_probabilities * conditional_log_probabilities
    ).sum(dim=2)
    entropy = group_entropy + (group_probabilities * local_entropy_by_group).sum(dim=1)
    return PolicyDistribution(
        joint_probabilities,
        joint_log_probabilities,
        group_probabilities,
        group_log_probabilities,
        conditional_probabilities,
        conditional_log_probabilities,
        entropy,
    )


def factorized_kl(
    current: PolicyDistribution, reference: PolicyDistribution
) -> torch.Tensor:
    """Exact KL(current || reference) for the shared hierarchical support."""
    group_kl = (
        current.group_probabilities
        * (current.group_log_probabilities - reference.group_log_probabilities)
    ).sum(dim=1)
    local_kl = (
        current.conditional_probabilities
        * (
            current.conditional_log_probabilities
            - reference.conditional_log_probabilities
        )
    ).sum(dim=2)
    return group_kl + (current.group_probabilities * local_kl).sum(dim=1)


class ResidualBlock(nn.Module):
    def __init__(self, width: int) -> None:
        super().__init__()
        self.norm = nn.LayerNorm(width)
        self.first = nn.Linear(width, width)
        self.second = nn.Linear(width, width)

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        hidden = self.first(self.norm(value))
        hidden = F.silu(hidden)
        return F.silu(value + self.second(hidden))


class AtaraxosPolicyValue(nn.Module):
    """Shared policy/value trunk; each legal card choice owns one primary score."""

    def __init__(
        self,
        observation_size: int,
        card_count: int,
        *,
        width: int = 240,
        blocks: int = 2,
    ) -> None:
        super().__init__()
        if observation_size < 1 or card_count < 1 or width < 1 or blocks < 1:
            raise ValueError("model dimensions must be positive")
        self.observation_size = observation_size
        self.card_count = card_count
        self.width = width
        self.input = nn.Linear(observation_size, width)
        self.input_norm = nn.LayerNorm(width)
        self.blocks = nn.ModuleList(ResidualBlock(width) for _ in range(blocks))

        self.kind_embedding = nn.Embedding(6, 24)
        self.card_embedding = nn.Embedding(card_count + 1, 48, padding_idx=0)
        self.row_embedding = nn.Embedding(4, 8, padding_idx=0)
        self.action_projection = nn.Linear(24 + 3 * 48 + 8, width)
        self.group_projection = nn.Linear(24 + 48, width)
        self.local_actor = nn.Sequential(
            nn.Linear(2 * width, 128),
            nn.Tanh(),
            nn.Linear(128, 1),
        )
        self.group_actor = nn.Sequential(
            nn.Linear(2 * width, 128),
            nn.Tanh(),
            nn.Linear(128, 1),
        )
        self.value_head = nn.Sequential(
            nn.Linear(width, 128),
            nn.Tanh(),
            nn.Linear(128, 3),
        )

    def forward(
        self,
        observations: torch.Tensor,
        action_features: torch.Tensor,
        action_group_ids: torch.Tensor,
        legal_mask: torch.Tensor,
        group_features: torch.Tensor,
        group_mask: torch.Tensor,
    ) -> tuple[PolicyDistribution, torch.Tensor, torch.Tensor]:
        trunk, group_logits, local_logits = self.logits(
            observations, action_features, group_features
        )
        distribution = factorized_distribution(
            group_logits, local_logits, action_group_ids, legal_mask, group_mask
        )
        value_probabilities = torch.softmax(self.value_head(trunk), dim=-1)
        value = value_probabilities[..., 1] * 0.5 + value_probabilities[..., 2]
        return distribution, value, value_probabilities

    def logits(
        self,
        observations: torch.Tensor,
        action_features: torch.Tensor,
        group_features: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Return the trunk, the raw group logits and the raw local (per-action) logits.

        Padded groups and actions get logits too; callers mask them. The mod's
        temperature-zero rule reads these logits (`learning.policy.served_argmax`).
        """
        if observations.ndim != 2 or observations.size(1) != self.observation_size:
            raise ValueError("observation tensor does not match the frozen schema")
        if action_features.ndim != 3 or action_features.size(2) != 5:
            raise ValueError("action features must have shape [batch, actions, 5]")
        trunk = F.silu(self.input_norm(self.input(observations)))
        for block in self.blocks:
            trunk = block(trunk)

        action_kind = action_features[..., 0].clamp(
            0, self.kind_embedding.num_embeddings - 1
        )
        card = action_features[..., 1].clamp(0, self.card_count)
        target = action_features[..., 2].clamp(0, self.card_count)
        row = action_features[..., 3].clamp(0, self.row_embedding.num_embeddings - 1)
        template = action_features[..., 4].clamp(0, self.card_count)
        action_code = torch.cat(
            (
                self.kind_embedding(action_kind),
                self.card_embedding(card),
                self.card_embedding(target),
                self.row_embedding(row),
                self.card_embedding(template),
            ),
            dim=-1,
        )
        action_code = F.silu(self.action_projection(action_code))

        group_kind = group_features[..., 0].clamp(
            0, self.kind_embedding.num_embeddings - 1
        )
        group_card = group_features[..., 1].clamp(0, self.card_count)
        group_code = torch.cat(
            (self.kind_embedding(group_kind), self.card_embedding(group_card)), dim=-1
        )
        group_code = F.silu(self.group_projection(group_code))

        action_state = trunk[:, None, :].expand(-1, action_code.size(1), -1)
        group_state = trunk[:, None, :].expand(-1, group_code.size(1), -1)
        local_logits = self.local_actor(
            torch.cat((action_state, action_code), dim=-1)
        ).squeeze(-1)
        group_logits = self.group_actor(
            torch.cat((group_state, group_code), dim=-1)
        ).squeeze(-1)
        return trunk, group_logits, local_logits


def sample_action_indices(
    distribution: PolicyDistribution,
    action_group_ids: torch.Tensor,
    legal_mask: torch.Tensor,
    *,
    generator: torch.Generator,
) -> torch.Tensor:
    """Sample a primary group, then one legal placement/target in that group."""
    if action_group_ids.shape != legal_mask.shape:
        raise ValueError("action groups and legal mask must align")
    if torch.any(~legal_mask.any(dim=1)):
        raise ValueError("cannot sample a state without a legal action")
    chosen_groups = torch.multinomial(
        distribution.group_probabilities, 1, generator=generator
    ).squeeze(1)
    row_indices = torch.arange(legal_mask.size(0), device=legal_mask.device)
    local_weights = distribution.conditional_probabilities[row_indices, chosen_groups]
    local_mask = legal_mask & (action_group_ids == chosen_groups[:, None])
    return torch.multinomial(
        local_weights.masked_fill(~local_mask, 0.0),
        1,
        generator=generator,
    ).squeeze(1)
