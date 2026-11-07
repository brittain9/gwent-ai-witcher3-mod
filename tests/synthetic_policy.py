"""Self-contained model/schema fixtures; no game engine, game data or records."""

from __future__ import annotations

import json
from pathlib import Path

import torch

from learning.catalog_identity import catalog_identity
from learning.encoding import ACTION_SCHEMA_ID, OBSERVATION_SCHEMA_ID, observation_size
from learning.model import AtaraxosPolicyValue
from scripts.create_policy_fixtures import synthetic_cases

CARD_IDS = [9001, 9002]


def synthetic_suite() -> tuple[dict, dict]:
    return {}, {
        "schema_version": 1,
        "runtime_mappings": {},
        "decks": [],
        "cards": [{"id": card_id, "effects": []} for card_id in CARD_IDS],
    }


def decision() -> tuple[dict, list[dict]]:
    case = synthetic_cases(CARD_IDS, count=9, seed=17)[8]
    return case["observation"], case["legal_actions"]


def exportable_checkpoint(path: Path, catalog_path: Path, seed: int) -> Path:
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        model = AtaraxosPolicyValue(
            observation_size(CARD_IDS), len(CARD_IDS), width=16, blocks=1
        )
    torch.save(
        {
            "identity": {
                "run_id": "synthetic-schema-test",
                "updates": 0,
                "catalog_sha256": catalog_identity(
                    json.loads(catalog_path.read_bytes())
                ),
                "card_ids": CARD_IDS,
            },
            "model_config": {
                "observation_schema_id": OBSERVATION_SCHEMA_ID,
                "action_schema_id": ACTION_SCHEMA_ID,
                "observation_size": observation_size(CARD_IDS),
                "card_count": len(CARD_IDS),
                "width": 16,
                "blocks": 1,
            },
            "policy_state": model.state_dict(),
        },
        path,
    )
    return path
