"""Allowed primary-view extraction; all records here are fictional test schemas."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path


def fictional_journal(root, *, mutate=None):
    checkpoint = "a" * 64
    view = {
        "seat": 0,
        "to_move": 0,
        "decision_id": 7,
        "terminal": None,
        "pending_choice": None,
        "hand": [],
        "players": [{"seat": 0}, {"seat": 1}],
    }
    events = [
        {
            "event": "episode_start",
            "episode_id": "fictional:0",
            "data_kind": "gameplay",
            "observation_profile_id": "faction-unknown-v1",
            "provenance": {"candidate_sha256": checkpoint},
            "game_config": {"decks": "must never enter model input"},
        },
        {
            "event": "preparation",
            "record": {
                "observation": view,
                "legal_actions": [{"id": "pass", "kind": "pass"}],
                "acting_seat": 0,
                "raw_decision_id": 19,
                "encoded_position": {"observations": [0.0]},
            },
        },
        {
            "event": "episode_end",
            "result": {"status": "complete", "primary_decisions": 1},
        },
    ]
    if mutate:
        mutate(events)
    path = root / "episode.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"sequence": index, **event})
            for index, event in enumerate(events)
        )
        + "\n"
    )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }, checkpoint


class PrimaryProbeInputTests(unittest.TestCase):
    def test_only_allowed_primary_inputs_and_distinct_counters_are_retained(self):
        from shared_player.primary_probes import read_primary_journals

        with tempfile.TemporaryDirectory() as directory:
            spec, checkpoint = fictional_journal(Path(directory))
            result = read_primary_journals(
                [spec],
                checkpoint_sha256=checkpoint,
                observation_profile_id="faction-unknown-v1",
            )
            self.assertEqual(len(result["states"]), 1)
            state = result["states"][0]
            self.assertEqual(state["observation"]["decision_id"], 7)
            self.assertNotIn("game_config", state)
            self.assertNotIn("raw_decision_id", state["observation"])
            self.assertEqual(result["origins"][0]["raw_decision_id"], 19)
            self.assertEqual(result["source_journals"], [spec])

    def test_privileged_pending_incomplete_or_changed_sources_are_refused(self):
        from shared_player.primary_probes import (
            PrimaryProbeInputError,
            read_primary_journals,
        )

        mutations = [
            lambda events: events[1]["record"]["observation"].update(
                opponent_hand=[99]
            ),
            lambda events: events[1]["record"]["observation"]["players"][1].update(
                hand=[99]
            ),
            lambda events: events[1]["record"]["observation"].update(
                pending_choice={"kind": "nurse"}
            ),
            lambda events: events[2]["result"].update(status="incomplete"),
        ]
        for mutate in mutations:
            with (
                self.subTest(mutation=mutate),
                tempfile.TemporaryDirectory() as directory,
            ):
                spec, checkpoint = fictional_journal(Path(directory), mutate=mutate)
                with self.assertRaises(PrimaryProbeInputError):
                    read_primary_journals(
                        [spec],
                        checkpoint_sha256=checkpoint,
                        observation_profile_id="faction-unknown-v1",
                    )
        with tempfile.TemporaryDirectory() as directory:
            spec, checkpoint = fictional_journal(Path(directory))
            spec["sha256"] = "b" * 64
            with self.assertRaises(PrimaryProbeInputError):
                read_primary_journals(
                    [spec],
                    checkpoint_sha256=checkpoint,
                    observation_profile_id="faction-unknown-v1",
                )


if __name__ == "__main__":
    unittest.main()
