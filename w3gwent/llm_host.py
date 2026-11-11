"""Trusted match host. Only fixed-seat packets cross the player boundary.

The engine, match configuration and command journal are privileged. This module
does not sandbox a player that already has access to those files or processes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import subprocess
import threading
import uuid
from pathlib import Path

SCHEMA = "w3gwent-llm-session-v1"
MAX_BYTES = 16 * 1024 * 1024
# Mechanics of the supported engine, not move recommendations. Legal actions
# remain authoritative for targets, availability and pending choices.
EFFECT_DESCRIPTIONS = {
    0: "No additional effect.",
    1: "Unsupported effect in this engine.",
    2: "Unsupported effect in this engine.",
    3: "Unsupported effect in this engine.",
    4: "Unsupported effect in this engine.",
    5: "Unsupported effect in this engine.",
    6: "Unsupported effect in this engine.",
    7: "Melee Scorch: destroy tied strongest eligible enemy melee units when that row totals at least 10.",
    8: "Passive leader: draw one extra opening card when this leader applies.",
    9: "Leader: remove all weather from both boards.",
    10: "Leader: play a weather card from your deck; resolve a weather choice if offered.",
    11: "Leader: play Rain from your deck, affecting siege rows on both boards.",
    12: "Leader: play Fog from your deck, affecting ranged rows on both boards.",
    13: "Leader: play Frost from your deck, affecting melee rows on both boards.",
    14: "Leader: privately reveal up to three random enemy hand cards. An AI-kind seat receives no reveal.",
    15: "Leader: move an eligible card from your graveyard into your hand.",
    16: "Leader: move an eligible card from the enemy graveyard into your hand.",
    17: "Leader: discard two hand cards, then pick a card from your deck. This has no effect for an AI-kind seat.",
    18: "Leader: place a Horn on your melee row.",
    19: "Leader: place a Horn on your ranged row.",
    20: "Leader: place a Horn on your siege row.",
    21: "Siege Scorch: destroy tied strongest eligible enemy siege units when that row totals at least 10.",
    22: "Counter King: disable leader abilities when the two leader templates differ.",
    23: "Frost: reduce susceptible melee units on both boards to at most one base power before buffs.",
    24: "Fog: reduce susceptible ranged units on both boards to at most one base power before buffs.",
    25: "Rain: reduce susceptible siege units on both boards to at most one base power before buffs.",
    26: "Decoy: replace an eligible unit on your board and return that unit to your hand.",
    27: "Horn: double eligible strength in the row. A unit Horn does not buff itself. Multiple Horns do not stack.",
    28: "Draw effect: unsupported in this engine.",
    29: "Global Scorch: destroy all tied strongest eligible non-hero units across both boards.",
    30: "Clear Weather: remove all weather from both boards.",
    31: "Muster: play the templates in summons from your deck and hand.",
    32: "Morale: add one strength to each other eligible unit in the same row.",
    33: "Medic: play an eligible unit from your graveyard; resolve its entry effects.",
    34: "Draw up to two cards from your deck when this unit enters play.",
    35: "Tight Bond: multiply eligible strength by the number of linked units already in the same row. Linked cards in hand and deck do not count. This effect does not summon them.",
    36: "Leader: reposition eligible Agile units between melee and ranged rows.",
    37: "Passive leader: make eligible Medic and leader resurrection targets random for both players.",
    38: "Passive leader: double non-hero spy strength for both players before other buffs.",
    39: "Ranged Scorch: destroy tied strongest eligible enemy ranged units when that row totals at least 10.",
    40: "Death summon: create the template in summons when this card is destroyed or sent to the graveyard during round cleanup.",
    41: "Mardroeme: transform eligible Berserkers in the same row into their summons template.",
    42: "Berserker: transform into the summons template when Mardroeme affects this row.",
    43: "Passive leader: weathered non-hero units on your board use half base power, rounded down, before buffs. Counter King cancels this.",
    44: "Leader: return eligible units from both graveyards to their respective decks.",
}
TYPE_BITS = {
    "1": "Melee row",
    "2": "Ranged row",
    "4": "Siege row",
    "8": "Unit",
    "16": "Weather",
    "32": "Spell",
    "64": "Row modifier",
    "128": "Hero",
    "256": "Spy: placed on the opposing board",
    "512": "Friendly effect",
    "1024": "Offensive effect",
    "2048": "Global effect",
}
OBSERVATION_FIELDS = {
    "seat",
    "to_move",
    "initiative",
    "decision_id",
    "round",
    "rounds_played",
    "lives",
    "scores",
    "round_results",
    "hand",
    "players",
    "weather",
    "pending_choice",
    "terminal",
}
CARD_FIELDS = {
    "instance_id",
    "template_id",
    "name",
    "power",
    "types",
    "effects",
    "owner",
    "controller",
    "score",
}
PLAYER_FIELDS = {
    "seat",
    "hand_count",
    "deck_count",
    "passed",
    "leader_used",
    "leader",
    "graveyard",
    "rows",
    "modifiers",
}


class HostError(ValueError):
    """A redacted connector failure safe to report to a player."""


class _Engine:
    def __init__(self, command, timeout):
        self.command = (
            [str(command)] if isinstance(command, (str, Path)) else list(command)
        )
        self.timeout = timeout
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise HostError("engine could not start") from None

    def request(self, request):
        payload = json.dumps(request, allow_nan=False).encode() + b"\n"
        if len(payload) > MAX_BYTES:
            raise HostError("engine request exceeds size limit")
        result = queue.Queue(maxsize=1)

        def exchange():
            try:
                self.process.stdin.write(payload)
                self.process.stdin.flush()
                raw = self.process.stdout.readline(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
                    raise ValueError
                response = json.loads(raw)
                if (
                    type(response) is not dict
                    or response.get("schema_version") != 1
                    or response.get("ok") is not True
                    or type(response.get("result")) is not dict
                ):
                    raise ValueError
                result.put(response["result"])
            except (OSError, ValueError, AttributeError):
                result.put(None)

        worker = threading.Thread(target=exchange, daemon=True)
        worker.start()
        try:
            response = result.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            raise HostError("engine response timed out; session stopped") from None
        if response is None:
            self.close()
            raise HostError("engine request failed; session stopped")
        return response

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        for pipe in (self.process.stdin, self.process.stdout):
            if pipe:
                pipe.close()


class MatchHost:
    """Own one engine process, one match and one fixed LLM seat."""

    def __init__(
        self,
        engine_command,
        new_match,
        opponent,
        *,
        seat=0,
        journal_path,
        timeout=30.0,
        max_actions=512,
        metadata=None,
    ):
        if type(seat) is not int or seat not in (0, 1):
            raise HostError("seat must be 0 or 1")
        if type(timeout) not in (int, float) or not 0 < timeout <= 600:
            raise HostError("timeout must be between 0 and 600 seconds")
        if type(max_actions) is not int or not 1 <= max_actions <= 10000:
            raise HostError("max_actions must be between 1 and 10000")
        if type(opponent) is not dict or opponent.get("op") not in {
            "stock_act",
            "network_search",
            "network_tree",
            "network_probe",
        }:
            raise HostError("unsupported opponent operation")
        if opponent["op"] in {"network_search", "network_tree"} and opponent.get(
            "belief"
        ) not in {"faction_pool", "recipe_public_v1"}:
            raise HostError("search requires an explicit fair belief")
        if opponent["op"] == "network_probe" and set(opponent) != {
            "op",
            "policy_metadata",
            "policy_weights",
        }:
            raise HostError(
                "direct network accepts only model paths; controller is group-first argmax"
            )
        if any(
            key in opponent
            for key in ("match_id", "seat", "schema_version", "request_id")
        ):
            raise HostError("opponent configuration overrides host identity")
        self.path = Path(journal_path)
        if self.path.exists():
            raise HostError("journal already exists")
        self.seat = seat
        self.session_id = uuid.uuid4().hex
        self.config = copy.deepcopy(new_match)
        self.opponent = copy.deepcopy(opponent)
        self.max_actions = max_actions
        self.lock = threading.RLock()
        self.closed = False
        self.lease = None
        self.refs = {}
        self.known_templates = set()
        self.history = []
        self.policy_loaded = False
        self.journal = {
            "schema": SCHEMA,
            "session_id": self.session_id,
            "commands": [],
            "seat": seat,
            "opponent": self.opponent,
            "metadata": metadata or {},
            "completed": False,
            "status": "active",
            "actions": 0,
        }
        self.engine = _Engine(engine_command, timeout)
        try:
            self.journal["policy_files"] = {}
            for key in ("policy_metadata", "policy_weights"):
                if key in self.opponent:
                    digest = hashlib.sha256(
                        Path(self.opponent[key]).read_bytes()
                    ).hexdigest()
                    self.journal["policy_files"][key] = digest
            executable = Path(self.engine.command[0])
            self.journal["engine_sha256"] = hashlib.sha256(
                executable.read_bytes()
            ).hexdigest()
            created = self._request(
                "new",
                **{
                    k: v
                    for k, v in self.config.items()
                    if k not in {"op", "schema_version", "request_id"}
                },
            )
            self.match_id = created["match_id"]
            self._save()
        except Exception as error:
            self.engine.close()
            self.closed = True
            self.journal["status"] = "failed"
            self._save()
            if isinstance(error, HostError):
                raise
            raise HostError("engine initialization failed") from None

    def _request(self, op, **fields):
        request = {"schema_version": 1, "op": op, **fields}
        try:
            verify_policy = (
                op in {"network_probe", "network_search", "network_tree"}
                and not self.policy_loaded
            )
            if verify_policy:
                self._check_policy_files()
            response = self.engine.request(request)
            if verify_policy:
                self._check_policy_files()
                self.policy_loaded = True
        except HostError as error:
            self._fail(str(error), op)
        self.journal["commands"].append({"request": request, "response": response})
        return response

    def _fail(self, message, operation):
        self.engine.close()
        self.closed = True
        self.lease = None
        self.journal["status"] = "failed"
        self.journal["failed_operation"] = operation
        self.journal["failure_reason"] = message
        self._save()
        raise HostError(message)

    def _check_policy_files(self):
        try:
            for key, expected in self.journal["policy_files"].items():
                actual = hashlib.sha256(
                    Path(self.opponent[key]).read_bytes()
                ).hexdigest()
                if actual != expected:
                    raise HostError("model files changed before first inference")
        except OSError:
            raise HostError("model files changed or became unreadable") from None

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            self.path.name + "." + uuid.uuid4().hex + ".tmp"
        )
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf8") as stream:
                json.dump(self.journal, stream, allow_nan=False)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _ref(self, instance):
        if instance not in self.refs:
            self.refs[instance] = "card-" + str(len(self.refs) + 1)
        return self.refs[instance]

    def _card(self, card):
        if card is None:
            return None
        if type(card.get("template_id")) is int:
            self.known_templates.add(card["template_id"])
        result = {k: copy.deepcopy(v) for k, v in card.items() if k in CARD_FIELDS}
        if "instance_id" in result:
            result["instance_id"] = self._ref(result["instance_id"])
        return result

    def _observe(self):
        observation = self._request("observe", match_id=self.match_id, seat=self.seat)[
            "observation"
        ]
        if observation.get("seat") != self.seat:
            self._fail("engine returned the wrong seat", "observe")
        result = {
            k: copy.deepcopy(v)
            for k, v in observation.items()
            if k in OBSERVATION_FIELDS
        }
        for zone in ("hand", "weather"):
            result[zone] = [self._card(card) for card in observation.get(zone, [])]
        result["players"] = []
        for player in observation.get("players", []):
            public = {
                k: copy.deepcopy(v) for k, v in player.items() if k in PLAYER_FIELDS
            }
            for zone in ("leader",):
                if zone in public:
                    public[zone] = self._card(public[zone])
            if "graveyard" in public:
                public["graveyard"] = [self._card(card) for card in public["graveyard"]]
            if "rows" in public:
                public["rows"] = {
                    row: {
                        "cards": [self._card(c) for c in entry["cards"]],
                        "score": entry["score"],
                    }
                    for row, entry in public["rows"].items()
                }
            if "modifiers" in public:
                public["modifiers"] = {
                    row: self._card(card) for row, card in public["modifiers"].items()
                }
            result["players"].append(public)
        pending = observation.get("pending_choice")
        if pending is not None:
            result["pending_choice"] = {
                k: copy.deepcopy(v)
                for k, v in pending.items()
                if k in {"kind", "seat", "used", "remaining", "graveyard_seat"}
                or (k == "templates" and pending.get("seat") == self.seat)
            }
        return result

    def state(self):
        with self.lock:
            if self.closed:
                raise HostError("session is closed")
            observation = self._observe()
            while (
                observation.get("terminal") is None
                and observation.get("to_move") != self.seat
            ):
                other = 1 - self.seat
                legal_other = self._request("legal", match_id=self.match_id, seat=other)
                if not legal_other.get("acting"):
                    self._fail("engine has no acting opponent", "legal")
                options = copy.deepcopy(self.opponent)
                op = options.pop("op")
                if op == "stock_act":
                    options.setdefault("rng_seed", uuid.uuid4().int & 0xFFFFFFFF)
                if op == "network_probe":
                    action_id = self._network_action(other, legal_other, options)
                else:
                    selection = self._request(
                        op, match_id=self.match_id, seat=other, **options
                    )
                    action_id = (
                        selection.get("action", {}).get("id")
                        if op == "stock_act"
                        else selection.get("action_id")
                    )
                choices = [
                    a for a in legal_other["legal_actions"] if a.get("id") == action_id
                ]
                if len(choices) != 1:
                    self._fail("opponent did not select a legal action", op)
                self._step(other, legal_other["decision_id"], choices[0])
                observation = self._observe()
            completed = observation.get("terminal") is not None
            legal = []
            if not completed:
                actions = self._request("legal", match_id=self.match_id, seat=self.seat)
                if not actions.get("acting"):
                    self._fail("player has no open decision", "legal")
                if actions.get("decision_id") != observation["decision_id"]:
                    self._fail("engine decision changed", "legal")
                for index, action in enumerate(actions["legal_actions"]):
                    exposed = {
                        k: copy.deepcopy(v)
                        for k, v in action.items()
                        if k
                        in {
                            "kind",
                            "row",
                            "template_id",
                            "instance_id",
                            "target_instance_id",
                        }
                    }
                    if (observation.get("pending_choice") or {}).get(
                        "kind"
                    ) == "coin_toss":
                        exposed["starting_seat"] = exposed.pop("template_id")
                    for key in ("instance_id", "target_instance_id"):
                        if exposed.get(key) is not None:
                            exposed[key] = self._ref(exposed[key])
                    exposed["id"] = f"option-{index + 1}"
                    legal.append(exposed)
                if (
                    self.lease is None
                    or self.lease["decision_id"] != observation["decision_id"]
                ):
                    self.lease = {
                        "decision_id": observation["decision_id"],
                        "token": uuid.uuid4().hex,
                        "actions": actions["legal_actions"],
                    }
            decks = self.config.get("decks", [[], []])
            deck = decks[self.seat]
            if isinstance(deck, dict):
                deck = deck["cards"]
            cards = self.config.get("cards", [])
            if isinstance(cards, dict):
                cards = cards.get("cards", [])
            known = (
                set(deck) | set(self.config.get("leaders", [])) | self.known_templates
            )
            special = (
                decks[self.seat].get("special_card")
                if isinstance(decks[self.seat], dict)
                else None
            )
            if special is not None:
                known.add(special)
            catalog = {card["id"]: card for card in cards}
            pending_templates = observation.get("pending_choice") or {}
            known.update(pending_templates.get("templates", []))
            while True:
                linked = {
                    summon
                    for template in known
                    for summon in catalog.get(template, {}).get("summons", [])
                }
                if linked <= known:
                    break
                known.update(linked)
            packet = {
                "schema": "w3gwent-llm-player-v1",
                "session_id": self.session_id,
                "seat": self.seat,
                "decision_id": observation["decision_id"],
                "token": self.lease["token"] if not completed else None,
                "observation": observation,
                "legal_actions": legal,
                "history": copy.deepcopy(self.history),
                "completed": completed,
                "setup": {
                    "deck": deck,
                    "special_card": special,
                    "leaders": copy.deepcopy(self.config.get("leaders", [])),
                    "factions": copy.deepcopy(self.config.get("factions", [0, 0])),
                    "seat_kind": copy.deepcopy(
                        self.config.get("seat_kind", ["human", "ai"])
                    ),
                    "cards": [
                        {
                            k: copy.deepcopy(v)
                            for k, v in card.items()
                            if k
                            in {
                                "id",
                                "name",
                                "display_name",
                                "power",
                                "faction",
                                "types",
                                "effects",
                                "summons",
                                "raw_flags",
                            }
                        }
                        for card in cards
                        if card.get("id") in known
                    ],
                    "effect_descriptions": {
                        str(effect): EFFECT_DESCRIPTIONS.get(
                            effect,
                            "Unknown effect; no mechanics reference is available.",
                        )
                        for template in known
                        for effect in catalog.get(template, {}).get("effects", [])
                    },
                    "type_bits": copy.deepcopy(TYPE_BITS),
                },
            }
            return packet

    def _network_action(self, seat, legal, options):
        observation = self._request("observe", match_id=self.match_id, seat=seat)[
            "observation"
        ]
        selection = self._request(
            "network_probe",
            probes=[
                {"observation": observation, "legal_actions": legal["legal_actions"]}
            ],
            **options,
        )
        inference = selection["inferences"][0]
        group = max(
            range(len(inference["group_logits"])),
            key=lambda i: inference["group_logits"][i],
        )
        members = [i for i, g in enumerate(inference["action_groups"]) if g == group]
        selected = max(members, key=lambda i: inference["local_logits"][i])
        other = 1 - seat
        if (
            observation.get("pending_choice") is None
            and observation.get("lives", [2, 2])[other] == 1
            and observation.get("scores", [0, 0])[seat]
            > observation.get("scores", [0, 0])[other]
            and observation.get("players", [{}, {}])[other].get("passed") is True
        ):
            return "pass"
        return legal["legal_actions"][selected]["id"]

    @staticmethod
    def _locations(observation):
        result = {}

        def add(cards, zone):
            for card in cards:
                if card:
                    result[card["instance_id"]] = {"card": card, "zone": zone}

        add(observation.get("hand", []), "own_hand")
        add(observation.get("weather", []), "weather")
        for seat, player in enumerate(observation.get("players", [])):
            add(player.get("graveyard", []), f"seat_{seat}_graveyard")
            for row, entry in player.get("rows", {}).items():
                add(entry["cards"], f"seat_{seat}_{row}")
            add(list(player.get("modifiers", {}).values()), f"seat_{seat}_modifiers")
        return result

    def _step(self, seat, decision_id, action, note=""):
        if self.journal["actions"] >= self.max_actions:
            self.journal["status"] = "action_limit"
            self._save()
            raise HostError("session action limit reached")
        before = self._locations(self._observe())
        transition = self._request(
            "step",
            match_id=self.match_id,
            seat=seat,
            decision_id=decision_id,
            action=action,
        )["transition"]
        self.journal["actions"] += 1
        item = {"actor": seat, "decision_id": decision_id, "kind": action.get("kind")}
        event = transition.get("event", {})
        if event.get("type") == "information" and event.get("visible_to") == self.seat:
            item["information"] = [self._card(c) for c in event.get("cards", [])]
        observed = self._observe()
        after = self._locations(observed)
        item["changes"] = [
            {
                "card": (after.get(ref) or before[ref])["card"],
                "from": before.get(ref, {}).get("zone"),
                "to": after.get(ref, {}).get("zone"),
            }
            for ref in dict.fromkeys([*before, *after])
            if before.get(ref) != after.get(ref)
        ]
        item.update(
            {
                key: copy.deepcopy(observed[key])
                for key in ("round", "scores", "lives", "round_results")
                if key in observed
            }
        )
        self.history.append(item)
        if note:
            self.journal.setdefault("notes", []).append(
                {
                    "decision_id": decision_id,
                    "seat": seat,
                    "note": note,
                    "status": "unvalidated_hypothesis",
                }
            )
        self.journal["completed"] = observed.get("terminal") is not None
        if self.journal["completed"]:
            self.journal["status"] = "completed"
        self.lease = None
        self._save()

    def act(self, action_id, *, decision_id, token, note=""):
        with self.lock:
            if self.closed or self.lease is None:
                raise HostError("no open player decision")
            if (
                type(decision_id) is not int
                or decision_id != self.lease["decision_id"]
                or type(token) is not str
                or token != self.lease["token"]
            ):
                raise HostError("stale or mismatched decision")
            if type(note) is not str or len(note) > 2000:
                raise HostError("note must be at most 2000 characters")
            choices = {
                f"option-{i + 1}": a for i, a in enumerate(self.lease["actions"])
            }
            if type(action_id) is not str or action_id not in choices:
                raise HostError("action is not in the current legal set")
            legal = self._request("legal", match_id=self.match_id, seat=self.seat)
            if (
                not legal.get("acting")
                or legal.get("decision_id") != decision_id
                or choices[action_id] not in legal.get("legal_actions", [])
            ):
                self.lease = None
                raise HostError("stale decision or changed legal actions")
            self._step(self.seat, decision_id, choices[action_id], note)
            return self.state()

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.engine.close()
                if self.journal["status"] == "active":
                    self.journal["status"] = "closed_incomplete"
                self._save()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def replay_journal(engine_command, journal_path, *, timeout=30.0):
    """Reproduce recorded engine transitions, not LLM quality or native parity."""
    try:
        path = Path(journal_path)
        if path.stat().st_size > 256 * MAX_BYTES:
            raise HostError("journal exceeds size limit")
        journal = json.loads(path.read_text(encoding="utf8"))
        if journal.get("schema") != SCHEMA or type(journal.get("commands")) is not list:
            raise HostError("invalid journal schema")
        engine = _Engine(engine_command, timeout)
        try:
            digest = hashlib.sha256(Path(engine.command[0]).read_bytes()).hexdigest()
            if digest != journal.get("engine_sha256"):
                raise HostError("replay engine identity differs")
            count = 0
            match_id = None
            for command in journal["commands"]:
                request = command["request"]
                if request["op"] not in {"new", "step", "stock_act"}:
                    continue
                actual = engine.request(request)
                if json.dumps(actual, sort_keys=True) != json.dumps(
                    command["response"], sort_keys=True
                ):
                    raise HostError("journal replay diverged")
                if request["op"] == "new":
                    if match_id is not None:
                        raise HostError("journal contains multiple matches")
                    match_id = actual["match_id"]
                count += request["op"] == "step"
            if match_id is None or count != journal.get("actions"):
                raise HostError("journal action count differs")
            observed = engine.request(
                {
                    "schema_version": 1,
                    "op": "observe",
                    "match_id": match_id,
                    "seat": journal["seat"],
                }
            )
            completed = observed["observation"].get("terminal") is not None
            if completed != journal.get("completed"):
                raise HostError("journal completion differs")
            return {"replayed_actions": count, "completed": completed}
        finally:
            engine.close()
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        raise HostError("invalid or unreadable journal") from None
