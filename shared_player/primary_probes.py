"""Hash-bound allowed primary positions and actual-checkpoint numeric probes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import struct
import time
from pathlib import Path

from .bundle import _object, _write


class PrimaryProbeInputError(ValueError):
    """Recorded positions do not meet the explicit primary-view contract."""


def _need(condition, message):
    if not condition:
        raise PrimaryProbeInputError(message)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_object)


def _file(spec):
    path = Path(spec["path"])
    _need(
        path.is_absolute() and path.is_file() and not path.is_symlink(),
        "explicit regular input required",
    )
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    _need(digest == spec["sha256"], "registered numeric input changed")
    return path


def read_primary_journals(journal_specs, *, checkpoint_sha256, observation_profile_id):
    """Extract only allowed preparations; retain raw IDs and origins separately."""
    _need(
        observation_profile_id in {"faction-unknown-v1", "full-faction-v1"},
        "explicit observation profile required",
    )
    _need(
        isinstance(journal_specs, list) and bool(journal_specs),
        "explicit journal inventory required",
    )
    states, origins, retained, seen_episodes, seen_files = [], [], [], set(), set()
    for spec in journal_specs:
        path = _file(spec)
        _need(str(path) not in seen_files, "duplicate journal input")
        seen_files.add(str(path))
        events = [
            json.loads(line, object_pairs_hook=_object)
            for line in path.read_text().splitlines()
            if line
        ]
        _need(
            bool(events)
            and [event.get("sequence") for event in events] == list(range(len(events))),
            "journal sequence is incomplete",
        )
        first, last = events[0], events[-1]
        _need(
            first.get("event") == "episode_start"
            and first.get("data_kind") == "gameplay"
            and first.get("observation_profile_id") == observation_profile_id,
            "journal has no matching gameplay/profile origin",
        )
        _need(
            first.get("provenance", {}).get("candidate_sha256") == checkpoint_sha256,
            "journal behavior checkpoint differs",
        )
        episode = first.get("episode_id")
        _need(
            isinstance(episode, str) and bool(episode) and episode not in seen_episodes,
            "journal episode identity is missing or duplicated",
        )
        seen_episodes.add(episode)
        _need(
            last.get("event") == "episode_end"
            and last.get("result", {}).get("status") == "complete",
            "only complete retained pilot episodes admitted to this numeric suite",
        )
        preparations = [
            event["record"] for event in events if event.get("event") == "preparation"
        ]
        _need(
            len(preparations) == last["result"].get("primary_decisions")
            and bool(preparations),
            "journal primary preparation count differs",
        )
        for ordinal, record in enumerate(preparations):
            view, legal, actor = (
                record.get("observation"),
                record.get("legal_actions"),
                record.get("acting_seat"),
            )
            _need(
                type(actor) is int
                and actor in (0, 1)
                and isinstance(view, dict)
                and type(view.get("seat")) is int
                and view["seat"] == actor
                and type(view.get("to_move")) is int
                and view["to_move"] == actor,
                "primary actor view differs",
            )
            _need(
                view.get("pending_choice") is None and view.get("terminal") is None,
                "only nonterminal primary inputs admitted",
            )
            _need(
                not {
                    "full_state",
                    "snapshot",
                    "opponent_hand",
                    "opponent_deck",
                    "hidden_state",
                }.intersection(view),
                "privileged fields are forbidden",
            )
            players = view.get("players")
            _need(
                isinstance(players, list)
                and len(players) == 2
                and all(
                    isinstance(player, dict) and type(player.get("seat")) is int
                    for player in players
                ),
                "two public player summaries required",
            )
            summaries = {player["seat"]: player for player in players}
            _need(
                set(summaries) == {0, 1}
                and not {
                    "hand",
                    "deck",
                    "hand_cards",
                    "deck_cards",
                    "deck_template_ids",
                }.intersection(summaries[1 - actor]),
                "opponent hidden cards are forbidden",
            )
            primary, raw = view.get("decision_id"), record.get("raw_decision_id")
            _need(
                type(primary) is int
                and primary >= 0
                and type(raw) is int
                and raw >= primary,
                "raw and primary counters must remain separate valid integers",
            )
            _need(
                isinstance(legal, list)
                and bool(legal)
                and all(
                    isinstance(action, dict)
                    and action.get("kind") in {"play", "pass", "leader"}
                    and isinstance(action.get("id"), str)
                    and action["id"]
                    for action in legal
                ),
                "canonical primary legal actions required",
            )
            _need(
                len({action["id"] for action in legal}) == len(legal),
                "canonical primary IDs must be unique",
            )
            _need(
                isinstance(record.get("encoded_position"), dict),
                "retained encoded input required",
            )
            states.append(copy.deepcopy({"observation": view, "legal_actions": legal}))
            retained.append(copy.deepcopy(record["encoded_position"]))
            origins.append(
                {
                    "journal_sha256": spec["sha256"],
                    "episode_id": episode,
                    "primary_ordinal": ordinal,
                    "acting_seat": actor,
                    "primary_turn": primary,
                    "raw_decision_id": raw,
                }
            )
    return {
        "states": states,
        "origins": origins,
        "retained_encodings": retained,
        "source_journals": copy.deepcopy(journal_specs),
    }


def preflight_reference_probes(manifest_path):
    """Keep recorded behavior identity separate from the evaluated reference."""
    path = Path(manifest_path)
    manifest = _read(path)
    _need(
        manifest.get("schema") == 2
        and manifest.get("purpose") == "shared-player-reference-checkpoint-probes",
        "explicit reference-checkpoint probe schema2 required",
    )
    _need(
        "reference_checkpoint" in manifest and "checkpoint" not in manifest,
        "reference checkpoint must be named without a legacy ambiguous key",
    )
    behavior = manifest.get("journal_behavior_checkpoint_sha256")
    _need(
        isinstance(behavior, str) and re.fullmatch("[0-9a-f]{64}", behavior),
        "explicit authentic journal behavior checkpoint required",
    )
    checkpoint_path, catalog_path = (
        _file(manifest["reference_checkpoint"]),
        _file(manifest["catalog"]),
    )
    profile = manifest["observation_profile_id"]
    positions = read_primary_journals(
        manifest["journals"], checkpoint_sha256=behavior, observation_profile_id=profile
    )
    _need(
        type(manifest.get("expected_primary_positions")) is int
        and manifest["expected_primary_positions"] > 0
        and len(positions["states"]) == manifest["expected_primary_positions"],
        "registered actual-primary count differs",
    )
    return {
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "profile": profile,
        "positions": positions,
        "reference_checkpoint_path": checkpoint_path,
        "catalog_path": catalog_path,
        "reference_checkpoint_sha256": manifest["reference_checkpoint"]["sha256"],
        "journal_behavior_checkpoint_sha256": behavior,
    }


def produce_probes(manifest_path, output_dir):
    """Produce independent Torch references only within a registered model job."""
    output_dir = Path(output_dir)
    _need(
        not output_dir.exists() and not output_dir.is_symlink(),
        "probe output must be fresh",
    )
    admitted = preflight_reference_probes(manifest_path)
    manifest, profile, positions = (
        admitted["manifest"],
        admitted["profile"],
        admitted["positions"],
    )
    checkpoint_path, catalog_path = (
        admitted["reference_checkpoint_path"],
        admitted["catalog_path"],
    )
    output_dir.mkdir(parents=True)
    _write(output_dir / "registered-inputs.json", manifest)
    stage, started = "model_import", time.perf_counter()
    try:
        import torch

        from learning.catalog_identity import catalog_identity
        from learning.policy import checkpoint_policy
        from scripts.create_policy_fixtures import _logits_and_choices
        from scripts.policy_probes import create_probes

        torch.set_num_threads(1)
        import_seconds = time.perf_counter() - started
        stage = "fictional_references"
        create_probes(
            {"shared": checkpoint_path},
            catalog_path,
            output_dir / "fictional.json",
            observation_profile_id=profile,
        )
        catalog = _read(catalog_path)
        semantic_catalog = catalog_identity(catalog)
        card_ids = sorted(int(card["id"]) for card in catalog["cards"])
        effects = [
            list(map(int, card.get("effects", [])))
            for card in sorted(catalog["cards"], key=lambda card: int(card["id"]))
        ]
        load_started = time.perf_counter()
        policy = checkpoint_policy(
            checkpoint_path,
            catalog,
            catalog_sha256=semantic_catalog,
            observation_profile_id=profile,
            device="cpu",
        )
        load_seconds = time.perf_counter() - load_started
        stage = "actual_primary_references"
        states = positions["states"]
        predictions = [
            prediction
            for start in range(0, len(states), 10)
            for prediction in _logits_and_choices(
                policy, states[start : start + 10], card_ids
            )
        ]
        for prediction, retained in zip(
            predictions, positions["retained_encodings"], strict=True
        ):
            values = retained.get("observations")
            _need(
                isinstance(values, list)
                and all(
                    type(value) in (int, float) and math.isfinite(value)
                    for value in values
                ),
                "retained observation encoding is invalid",
            )
            encoded_sha = hashlib.sha256(
                b"".join(struct.pack("<f", value) for value in values)
            ).hexdigest()
            _need(
                encoded_sha == prediction["observation_sha256"],
                "retained observation encoding differs from actual checkpoint reference",
            )
            for field in ("action_features", "action_group_ids", "group_features"):
                _need(
                    retained.get(field) == prediction[field],
                    f"retained {field} differs",
                )
            _need(
                retained.get("observation_profile_id") == profile
                and retained.get("legal_mask")
                == [True] * len(prediction["action_features"])
                and retained.get("group_mask")
                == [True] * len(prediction["group_features"]),
                "retained masks or profile differ",
            )
        header = {
            "checkpoint_sha256": admitted["reference_checkpoint_sha256"],
            "journal_behavior_checkpoint_sha256": admitted[
                "journal_behavior_checkpoint_sha256"
            ],
            "catalog_sha256": semantic_catalog,
            "observation_profile_id": profile,
            "counter_contract": "completed-primary-turns-v1",
            "decision_source": "retained allowed primary views from frozen development simulator pilot",
            "rules_validated": False,
            "dev_game_count": len(manifest["journals"]),
            "position_count": len(states),
            "source_journals": manifest["journals"],
        }
        model = {
            "card_ids": card_ids,
            "card_effects": effects,
            "header": header,
            "cases": [
                {"position_id": index, **state, "origin": origin, "expected": expected}
                for index, (state, origin, expected) in enumerate(
                    zip(states, positions["origins"], predictions, strict=True)
                )
            ],
        }
        _write(
            output_dir / "actual-primary.json",
            {
                "format": "witcher3-shared-actual-primary-probes-v1",
                "models": {admitted["reference_checkpoint_sha256"]: model},
            },
        )
        _file(manifest["reference_checkpoint"])
        _file(manifest["catalog"])
        read_primary_journals(
            manifest["journals"],
            checkpoint_sha256=admitted["journal_behavior_checkpoint_sha256"],
            observation_profile_id=profile,
        )
        report = {
            "status": "complete",
            "fictional_positions": 200,
            "actual_primary_positions": len(states),
            "reference_checkpoint_sha256": admitted["reference_checkpoint_sha256"],
            "journal_behavior_checkpoint_sha256": admitted[
                "journal_behavior_checkpoint_sha256"
            ],
            "source_journals": manifest["journals"],
            "torch_import_seconds": import_seconds,
            "actual_policy_load_seconds": load_seconds,
            "wall_seconds": time.perf_counter() - started,
            "information_protocol": "actor-seat-view-only",
            "rules_validated": False,
            "player_latency_evidence": False,
            "strength_evidence": False,
            "teacher_or_targets_admitted": False,
        }
        _need(
            hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
            == admitted["manifest_sha256"],
            "reference probe manifest changed during production",
        )
        _write(output_dir / "result.json", report)
        return report
    except Exception as error:
        _write(
            output_dir / "failure.json",
            {
                "stage": stage,
                "type": type(error).__name__,
                "message": str(error),
                "wall_seconds": time.perf_counter() - started,
                "strength_evidence": False,
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(produce_probes(args.manifest, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
