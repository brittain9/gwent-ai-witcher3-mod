"""Player-facing connector contracts over the line-JSON process boundary."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from w3gwent.llm_host import HostError, MatchHost, replay_journal

ENGINE = """import json, sys
decision = 0
for line in sys.stdin:
    q = json.loads(line)
    op = q['op']
    obs = {'seat': q.get('seat', 0), 'decision_id': decision,
           'to_move': 0 if decision == 0 else 1, 'hand': [
               {'instance_id': 9876, 'template_id': 1, 'name': 'Visible'}],
           'players': [{'hand_count': 1}, {'hand_count': 1}],
           'terminal': {'winner': 0} if decision >= 2 else None}
    legal = [{'id': 'play:9876', 'kind': 'play', 'instance_id': 9876}]
    if op == 'new': r = {'match_id': 'm1', 'rng_trace': ['PRIVATE'], 'actor': 0}
    elif op == 'observe': r = {'observation': obs}
    elif op == 'legal': r = {'decision_id': decision, 'acting': True, 'legal_actions': legal}
    elif op == 'stock_act': r = {'action': legal[0], 'rng_trace': ['PRIVATE']}
    elif op == 'step':
        decision += 1
        r = {'transition': {'decision_id': decision, 'event': {'type': 'played'}},
             'rng_trace': ['PRIVATE']}
    elif op == 'drop': r = {'dropped': True}
    print(json.dumps({'schema_version': 1, 'ok': True, 'result': r}), flush=True)
"""


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = self.root / "engine.py"
        self.engine.write_text(ENGINE)
        self.command = [sys.executable, str(self.engine)]
        self.config = {
            "cards": [
                {"id": 1, "name": "Visible"},
                {"id": 2, "name": "HIDDEN OPPONENT"},
            ],
            "decks": [[1], [2]],
            "leaders": [1, 1],
            "seed": 12345,
        }

    def host(self, **options):
        host = MatchHost(
            self.command,
            self.config,
            {"op": "stock_act"},
            journal_path=self.root / "journal.json",
            **options,
        )
        self.addCleanup(host.close)
        return host

    def test_player_sees_own_cards_and_opaque_legal_choices(self):
        packet = self.host().state()
        encoded = json.dumps(packet)
        for secret in ["PRIVATE", "HIDDEN OPPONENT", "9876", "12345", "rng_trace"]:
            self.assertNotIn(secret, encoded)
        self.assertEqual(packet["observation"]["hand"][0]["name"], "Visible")
        self.assertEqual(packet["setup"]["deck"], [1])
        self.assertEqual(len(packet["legal_actions"]), 1)

    def test_session_identity_is_shared_by_state_and_saved_journal(self):
        host = self.host()
        packet = host.state()
        self.assertEqual(packet["session_id"], host.session_id)
        self.assertTrue(packet["session_id"])
        host.close()
        journal = json.loads((self.root / "journal.json").read_text())
        self.assertEqual(journal["session_id"], packet["session_id"])

    def test_tight_bond_description_excludes_hand_and_deck_cards(self):
        self.config["cards"][0]["effects"] = [35]
        description = self.host().state()["setup"]["effect_descriptions"]["35"]
        self.assertIn("already in the same row", description)
        self.assertIn("hand and deck do not count", description)
        self.assertIn("does not summon", description)

    def test_visible_leader_effects_have_plain_language_mechanics(self):
        self.config["cards"][0].update({"effects": [12, 28], "types": 9})
        setup = self.host().state()["setup"]
        leader = next(
            card for card in setup["cards"] if card["id"] == setup["leaders"][0]
        )
        description = setup["effect_descriptions"][str(leader["effects"][0])]
        self.assertIn("Fog", description)
        self.assertIn("deck", description)
        self.assertIn("unsupported", setup["effect_descriptions"]["28"].lower())
        self.assertEqual(setup["type_bits"]["8"], "Unit")

    def test_legal_move_finishes_match_and_stale_or_illegal_moves_do_not_change_it(
        self,
    ):
        host = self.host()
        packet = host.state()
        for action, decision, token in [
            ("invented", 0, packet["token"]),
            ("option-1", 9, packet["token"]),
            ("option-1", 0, "wrong"),
            ("option-1", False, packet["token"]),
        ]:
            with self.assertRaises(HostError):
                host.act(action, decision_id=decision, token=token)
            self.assertEqual(host.state()["decision_id"], 0)
        ended = host.act(
            "option-1",
            decision_id=0,
            token=packet["token"],
            note="Try this legal move.",
        )
        self.assertTrue(ended["completed"])
        self.assertEqual(ended["decision_id"], 2)
        self.assertEqual(len(ended["history"]), 2)
        with self.assertRaises(HostError):
            host.act("option-1", decision_id=0, token=packet["token"])

    def test_completed_journal_replays_and_modified_action_is_rejected(self):
        host = self.host()
        packet = host.state()
        host.act("option-1", decision_id=0, token=packet["token"])
        host.close()
        path = self.root / "journal.json"
        self.assertEqual(
            replay_journal(self.command, path),
            {"replayed_actions": 2, "completed": True},
        )
        journal = json.loads(path.read_text())
        step = next(c for c in journal["commands"] if c["request"]["op"] == "step")
        step["response"]["transition"]["decision_id"] = 999
        path.write_text(json.dumps(journal))
        with self.assertRaises(HostError):
            replay_journal(self.command, path)

    def test_action_limit_reason_survives_close(self):
        host = self.host(max_actions=1)
        packet = host.state()
        with self.assertRaisesRegex(HostError, "action limit"):
            host.act("option-1", decision_id=0, token=packet["token"])
        host.close()
        journal = json.loads((self.root / "journal.json").read_text())
        self.assertEqual(journal["status"], "action_limit")
        self.assertFalse(journal["completed"])
        self.assertEqual(journal["actions"], 1)

    def test_engine_protocol_failures_stop_session_and_survive_close(self):
        cases = [
            (
                "illegal_opponent_action",
                ENGINE.replace("'action': legal[0]", "'action': {'id': 'unavailable'}"),
                True,
                "stock_act",
            ),
            (
                "wrong_observation_seat",
                ENGINE.replace("'seat': q.get('seat', 0)", "'seat': 1"),
                False,
                "observe",
            ),
            (
                "no_acting_player",
                ENGINE.replace("'acting': True", "'acting': False"),
                False,
                "legal",
            ),
            (
                "no_acting_opponent",
                ENGINE.replace("'acting': True", "'acting': decision == 0"),
                True,
                "legal",
            ),
            (
                "changed_legal_decision",
                ENGINE.replace(
                    "r = {'decision_id': decision, 'acting'",
                    "r = {'decision_id': decision + 1, 'acting'",
                ),
                False,
                "legal",
            ),
        ]
        for name, source, needs_move, operation in cases:
            with self.subTest(name=name):
                self.engine.write_text(source)
                path = self.root / (name + ".json")
                host = MatchHost(
                    self.command,
                    self.config,
                    {"op": "stock_act"},
                    journal_path=path,
                )
                self.addCleanup(host.close)
                with self.assertRaises(HostError):
                    packet = host.state()
                    if needs_move:
                        host.act("option-1", decision_id=0, token=packet["token"])
                with self.assertRaisesRegex(HostError, "session is closed"):
                    host.state()
                host.close()
                journal = json.loads(path.read_text())
                self.assertEqual(journal["status"], "failed")
                self.assertEqual(journal["failed_operation"], operation)
                self.assertFalse(journal["completed"])

    def test_search_requires_fair_belief(self):
        for belief in (None, "deck_list"):
            with self.assertRaises(HostError):
                MatchHost(
                    self.command,
                    self.config,
                    {"op": "network_search", "belief": belief},
                    journal_path=self.root / "rejected.json",
                )

    def test_journal_is_private_and_engine_timeout_is_redacted(self):
        host = self.host()
        self.assertEqual((self.root / "journal.json").stat().st_mode & 0o777, 0o600)
        host.close()
        self.engine.write_text("import time; time.sleep(10)")
        with self.assertRaisesRegex(HostError, "timed out"):
            MatchHost(
                self.command,
                self.config,
                {"op": "stock_act"},
                timeout=0.1,
                journal_path=self.root / "timeout.json",
            )
        self.assertEqual(
            json.loads((self.root / "timeout.json").read_text())["status"], "failed"
        )

    def test_direct_network_opponent_selects_group_before_local_action(self):
        self.engine.write_text(
            ENGINE.replace(
                "elif op == 'stock_act':",
                "elif op == 'network_probe': r = {'inferences': ["
                "{'group_logits': [10, 2], 'action_groups': [1, 0], 'local_logits': [100, 1]}]}\n"
                "    elif op == 'stock_act':",
            ).replace(
                "legal = [{'id': 'play:9876', 'kind': 'play', 'instance_id': 9876}]",
                "legal = [{'id': 'pass', 'kind': 'pass'}, "
                "{'id': 'play:9876', 'kind': 'play', 'instance_id': 9876}]",
            )
        )
        host = MatchHost(
            self.command,
            self.config,
            {
                "op": "network_probe",
                "policy_metadata": str(self.engine),
                "policy_weights": str(self.engine),
            },
            journal_path=self.root / "network.json",
        )
        self.addCleanup(host.close)
        packet = host.state()
        packet = host.act("option-1", decision_id=0, token=packet["token"])
        self.assertEqual(packet["history"][-1]["kind"], "play")

    def test_changed_model_file_is_rejected_before_first_inference(self):
        host = MatchHost(
            self.command,
            self.config,
            {
                "op": "network_probe",
                "policy_metadata": str(self.engine),
                "policy_weights": str(self.engine),
            },
            journal_path=self.root / "changed.json",
        )
        self.addCleanup(host.close)
        packet = host.state()
        self.engine.write_text(ENGINE + "\n# changed model payload\n")
        with self.assertRaisesRegex(HostError, "model files changed"):
            host.act("option-1", decision_id=0, token=packet["token"])

    def test_history_is_compact_and_opponent_private_reveals_stay_hidden(self):
        self.engine.write_text(
            ENGINE.replace(
                "{'type': 'played'}",
                "{'type': 'information', 'visible_to': 1, "
                "'cards': [{'instance_id': 9991, 'template_id': 2, 'name': 'SECRET REVEAL'}]}",
            )
        )
        self.config["factions"] = [3, 2]
        self.config["seat_kind"] = ["human", "ai"]
        host = self.host()
        packet = host.state()
        self.assertEqual(packet["setup"]["factions"], [3, 2])
        self.assertEqual(packet["setup"]["seat_kind"], ["human", "ai"])
        packet = host.act("option-1", decision_id=0, token=packet["token"])
        self.assertNotIn("SECRET REVEAL", json.dumps(packet))
        self.assertTrue(all("observation" not in item for item in packet["history"]))

    @unittest.skipUnless(
        os.environ.get("W3GWENT_ENGINE"), "set W3GWENT_ENGINE for native contract"
    )
    def test_native_scoia_choices_name_starting_seat_and_apply_selected_option(self):
        cards = [
            {
                "id": 9001,
                "name": "Synthetic infantry",
                "power": 5,
                "faction": 0,
                "types": 9,
                "effects": [],
                "summons": [],
            },
            {
                "id": 9999,
                "name": "Synthetic leader",
                "power": 0,
                "faction": 0,
                "types": 0,
                "effects": [9],
                "summons": [],
            },
        ]
        for selected_seat in (0, 1):
            config = {
                "cards": cards,
                "decks": [[9001] * 22, [9001] * 22],
                "factions": [4, 3],
                "leaders": [9999, 9999],
                "seed": 17,
                "seat_kind": ["human", "ai"],
            }
            with MatchHost(
                os.environ["W3GWENT_ENGINE"],
                config,
                {"op": "stock_act"},
                seat=0,
                journal_path=self.root / f"scoia-{selected_seat}.json",
            ) as host:
                packet = host.state()
                self.assertEqual(
                    packet["observation"]["pending_choice"]["kind"], "coin_toss"
                )
                self.assertEqual(
                    [action["starting_seat"] for action in packet["legal_actions"]],
                    [0, 1],
                )
                selected = packet["legal_actions"][selected_seat]
                self.assertTrue(selected["id"].startswith("option-"))
                self.assertNotIn("template_id", selected)
                result = host.act(
                    selected["id"],
                    decision_id=packet["decision_id"],
                    token=packet["token"],
                )
                self.assertEqual(result["observation"]["initiative"], selected_seat)

    @unittest.skipUnless(
        os.environ.get("W3GWENT_ENGINE"), "set W3GWENT_ENGINE for native contract"
    )
    def test_native_both_seats_complete_and_replay_with_private_reveal_memory(self):
        cards = [
            {
                "id": 9001,
                "name": "Synthetic infantry",
                "power": 5,
                "faction": 0,
                "types": 9,
                "effects": [],
                "summons": [],
            },
            {
                "id": 9999,
                "name": "Synthetic reveal leader",
                "power": 0,
                "faction": 0,
                "types": 0,
                "effects": [14],
                "summons": [],
            },
        ]
        command = os.environ["W3GWENT_ENGINE"]
        for seat in (0, 1):
            path = self.root / f"real-{seat}.json"
            config = {
                "cards": cards,
                "decks": [[9001] * 22, [9001] * 22],
                "leaders": [9999, 9999],
                "factions": [0, 0],
                "seed": 17,
                "seat_kind": ["human" if s == seat else "ai" for s in (0, 1)],
            }
            with MatchHost(
                command, config, {"op": "stock_act"}, seat=seat, journal_path=path
            ) as host:
                packet = host.state()
                while not packet["completed"]:
                    actions = packet["legal_actions"]
                    selected = next(
                        (a for a in actions if a["kind"] == "leader"), actions[0]
                    )
                    packet = host.act(
                        selected["id"],
                        decision_id=packet["decision_id"],
                        token=packet["token"],
                    )
                self.assertTrue(
                    any(item.get("information") for item in packet["history"])
                )
            result = replay_journal(command, path)
            self.assertTrue(result["completed"])
            self.assertGreater(result["replayed_actions"], 5)


if __name__ == "__main__":
    unittest.main()
