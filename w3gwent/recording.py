"""Read local in-game recordings and replay them through the native protocol."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

RECORDING_SCHEMA = "w3gwent-recorder-v1"
SETTINGS_SECTION = "W3GwentRecorder"
_CHUNK_KEY = re.compile(r"^m(\d{6})_(\d{6})$")
_COUNT_KEY = re.compile(r"^m(\d{6})_count$")
_STATUS_KEY = re.compile(r"^m(\d{6})_status$")


class RecordingError(ValueError):
    """A settings chunk, JSONL record, or replay contract is invalid."""


class ReplayDivergence(RuntimeError):
    """Replay stopped at the first difference from the recorded game."""

    def __init__(
        self,
        event_index: int,
        label: str,
        expected: Any,
        actual: Any,
        *,
        rng_cursor: int | None = None,
        chance_trace: Any = None,
        native_snapshot: dict[str, Any] | None = None,
        resume_after_event: int | None = None,
    ):
        self.event_index = event_index
        self.label = label
        self.expected = expected
        self.actual = actual
        self.rng_cursor = rng_cursor
        self.chance_trace = chance_trace
        self.native_snapshot = native_snapshot
        self.resume_after_event = resume_after_event
        path, left, right = first_difference(expected, actual)
        self.path = path
        self.expected_value = left
        self.actual_value = right
        detail = (
            f"event {event_index} {label} diverged at {path}: "
            f"recorded={left!r}; native={right!r}"
        )
        if rng_cursor is not None:
            detail += f"; rng_cursor={rng_cursor}"
        if chance_trace is not None:
            detail += f"; chance_site_trace={chance_trace!r}"
        super().__init__(detail)


def _settings_value(raw: str, *, where: str) -> str:
    value = raw.strip()
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RecordingError(f"{where}: malformed quoted settings value") from exc
        if not isinstance(decoded, str):
            raise RecordingError(f"{where}: settings value is not a string")
        return decoded
    return value


def read_settings_section(
    path: Path, section: str = SETTINGS_SECTION
) -> dict[str, str]:
    values: dict[str, str] = {}
    inside = False
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as exc:
        raise RecordingError(f"cannot read settings file {path}: {exc}") from exc
    except UnicodeError as exc:
        raise RecordingError(f"settings file is not UTF-8: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == f"[{section}]"
            continue
        if (
            not inside
            or not stripped
            or stripped.startswith((";", "#"))
            or "=" not in line
        ):
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        if key in values:
            raise RecordingError(f"{path}:{line_number}: duplicate key {key!r}")
        values[key] = _settings_value(raw, where=f"{path}:{line_number}")
    if not values:
        raise RecordingError(f"settings file has no [{section}] recording section")
    return values


def settings_recordings(values: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Validate and concatenate every numbered recorder match in one settings section."""
    grouped: dict[str, dict[str, Any]] = {}
    for key, value in values.items():
        if match := _CHUNK_KEY.fullmatch(key):
            match_id = f"m{match.group(1)}"
            grouped.setdefault(match_id, {"chunks": {}})["chunks"][
                int(match.group(2))
            ] = value
        elif match := _COUNT_KEY.fullmatch(key):
            grouped.setdefault(f"m{match.group(1)}", {"chunks": {}})["count"] = value
        elif match := _STATUS_KEY.fullmatch(key):
            grouped.setdefault(f"m{match.group(1)}", {"chunks": {}})["status"] = value
        elif key not in {"next_match", "active_match"}:
            raise RecordingError(f"unexpected key in recorder section: {key!r}")

    results: dict[str, dict[str, Any]] = {}
    for match_id, data in sorted(grouped.items()):
        raw_count = data.get("count")
        status = data.get("status")
        chunks = data["chunks"]
        if raw_count is None or not raw_count.isdecimal():
            raise RecordingError(f"{match_id}: missing or invalid chunk count")
        count = int(raw_count)
        if count < 1 or set(chunks) != set(range(count)):
            raise RecordingError(
                f"{match_id}: chunks do not match declared count {count}"
            )
        if not isinstance(status, str) or not status:
            raise RecordingError(f"{match_id}: recording was not flushed with a status")
        jsonl = "".join(chunks[index] for index in range(count))
        if not jsonl.endswith("\n"):
            raise RecordingError(f"{match_id}: final chunk ends inside a JSONL record")
        results[match_id] = {
            "match_id": match_id,
            "status": status,
            "chunk_count": count,
            "jsonl": jsonl,
        }
    if not results:
        raise RecordingError("recorder section contains no match chunks")
    return results


def load_recording(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except OSError as exc:
        raise RecordingError(f"cannot read recording {path}: {exc}") from exc
    except UnicodeError as exc:
        raise RecordingError(f"recording is not UTF-8: {path}") from exc
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise RecordingError(f"{path}:{line_number}: blank JSONL record")
        try:
            event = json.loads(line, parse_constant=_reject_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RecordingError(
                f"{path}:{line_number}: invalid JSON record: {exc}"
            ) from exc
        if not isinstance(event, dict):
            raise RecordingError(f"{path}:{line_number}: event must be an object")
        events.append(event)
    validate_recording(events)
    return events


def validate_recording(events: list[dict[str, Any]]) -> None:
    if len(events) < 2 or events[0].get("event") != "match_start":
        raise RecordingError("recording must start with a match_start header")
    header = events[0]
    if header.get("schema") != RECORDING_SCHEMA or header.get("schema_version") != 1:
        raise RecordingError("unsupported recording schema")
    if header.get("diagnostic") is not True or header.get("stock_ai") is not True:
        raise RecordingError(
            "recording must identify itself as a stock-AI diagnostic build"
        )
    required = {"cards", "decks", "factions", "special_cards", "seat_kinds"}
    if not required.issubset(header):
        raise RecordingError(f"match_start is missing {sorted(required - set(header))}")
    if (
        not isinstance(header["cards"], list)
        or not isinstance(header["decks"], list)
        or len(header["decks"]) != 2
    ):
        raise RecordingError("match_start needs a card catalog and two decks")
    if not isinstance(header["factions"], list) or len(header["factions"]) != 2:
        raise RecordingError("match_start needs two factions")
    if (
        not isinstance(header["special_cards"], list)
        or len(header["special_cards"]) != 2
    ):
        raise RecordingError("match_start needs two special cards")
    leaders = header.get("leaders")
    if (
        not isinstance(leaders, list)
        or len(leaders) != 2
        or any(type(value) is not int for value in leaders)
    ):
        raise RecordingError("match_start needs two leader template IDs")
    if not isinstance(header["seat_kinds"], list) or len(header["seat_kinds"]) != 2:
        raise RecordingError("match_start needs two seat kinds")
    if "stock_ai" not in header["seat_kinds"]:
        raise RecordingError("recording has no stock AI seat")
    if events[-1].get("event") != "match_end" or events[-1].get("reason") != "final":
        raise RecordingError("recording is incomplete; finish the match before replay")
    expected_call = 0
    for index, event in enumerate(events[1:], start=1):
        kind = event.get("event")
        if not isinstance(kind, str):
            raise RecordingError(f"event {index}: missing event name")
        if kind == "random":
            if event.get("call_index") != expected_call:
                raise RecordingError(
                    f"event {index}: random call sequence expected {expected_call}, "
                    f"found {event.get('call_index')!r}"
                )
            if (
                not isinstance(event.get("site"), str)
                or type(event.get("value")) not in (int, float)
                or not math.isfinite(event["value"])
            ):
                raise RecordingError(f"event {index}: invalid random call record")
            if not 0 <= event["value"] < 1:
                raise RecordingError(f"event {index}: random value is outside [0, 1)")
            expected_call += 1
        elif kind in {
            "stock_decision",
            "action",
            "state",
            "choice",
            "pass",
            "first_player",
            "chance",
            "shuffle",
            "draw",
        }:
            cursor = event.get("rng_cursor")
            if cursor is not None and (type(cursor) is not int or cursor < 0):
                raise RecordingError(
                    f"event {index}: rng_cursor must be a nonnegative integer"
                )
            continue
        elif kind != "match_end":
            raise RecordingError(f"event {index}: unknown event type {kind!r}")


def first_difference(
    expected: Any, actual: Any, path: str = "$"
) -> tuple[str, Any, Any]:
    """Return the first structural difference, keeping type changes visible."""
    if type(expected) is not type(actual):
        return path, expected, actual
    if isinstance(expected, dict):
        keys = sorted(set(expected) | set(actual))
        for key in keys:
            if key not in expected:
                return f"{path}.{key}", "<missing>", actual[key]
            if key not in actual:
                return f"{path}.{key}", expected[key], "<missing>"
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference[0]:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}.length", len(expected), len(actual)
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            difference = first_difference(left, right, f"{path}[{index}]")
            if difference[0]:
                return difference
    elif expected != actual:
        return path, expected, actual
    return "", None, None


def _transaction_for_decision(event: dict[str, Any]) -> dict[str, Any] | None:
    transaction = event.get("transaction")
    if transaction is not None and not isinstance(transaction, dict):
        raise RecordingError(f"event transaction must be an object or null: {event!r}")
    return transaction


def _engine_site_map(
    engine: Any, random_events: list[dict[str, Any]]
) -> dict[str, tuple[str | None, bool]]:
    capabilities = engine.capabilities()
    sites = capabilities.get("chance_sites")
    if not isinstance(sites, list):
        raise RecordingError("native capabilities do not list chance sites")
    site_map = {
        item["site"]: (
            item.get("engine_trace_site"),
            item.get("follows_as3_call_order") is True,
        )
        for item in sites
        if isinstance(item, dict) and isinstance(item.get("site"), str)
    }
    for call_index, event in enumerate(random_events):
        site = event["site"]
        if site not in site_map:
            raise RecordingError(
                f"random call {call_index}: site {site!r} is absent from native capabilities"
            )
        trace_site, follows_order = site_map[site]
        if not follows_order or trace_site is None:
            raise RecordingError(
                f"random call {call_index}: native engine cannot reproduce site {site!r}"
            )
    return site_map


def _decks(header: dict[str, Any]) -> list[list[int] | dict[str, Any]]:
    decks = header["decks"]
    specials = header["special_cards"]
    normalized: list[list[int] | dict[str, Any]] = []
    for seat, deck in enumerate(decks):
        if isinstance(deck, list):
            cards = deck
            special = specials[seat]
        elif isinstance(deck, dict) and isinstance(deck.get("cards"), list):
            cards = deck["cards"]
            special = deck.get("special_card", specials[seat])
        else:
            raise RecordingError(f"seat {seat} deck is malformed")
        if special is None or special == -1:
            normalized.append(cards)
        else:
            normalized.append({"cards": cards, "special_card": special})
    return normalized


def _match_id(result: dict[str, Any]) -> str:
    match_id = result.get("match_id")
    if not isinstance(match_id, str) or not match_id:
        raise RecordingError("native response has no match_id")
    return match_id


def _random_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("event") == "random"]


def _trace_events(events: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    trace_kinds = {"random", "chance", "shuffle", "draw", "first_player"}
    return [
        (index, event)
        for index, event in enumerate(events)
        if event.get("event") in trace_kinds
        and not (
            event.get("event") == "chance" and event.get("name") == "agile_tie_pick"
        )
    ]


def _ids(value: Any) -> Any:
    if isinstance(value, dict):
        if "instance_id" in value:
            return value["instance_id"]
        return {key: _ids(item) for key, item in value.items() if key != "list"}
    if isinstance(value, list):
        return [_ids(item) for item in value]
    return value


def _chance_name(name: Any) -> Any:
    return {"graveyard_random_revive": "skellige_random_resurrection"}.get(name, name)


def _trace_view(
    event: dict[str, Any],
    *,
    expected: bool,
    site_map: dict[str, tuple[str | None, bool]],
    stock_act: bool = False,
    special_cards: list[Any] | None = None,
) -> dict[str, Any]:
    kind = event.get("event")
    if kind == "random":
        result = {
            "event": "random",
            "call_index": event.get("call_index"),
            "value": event.get("value"),
            "rng_cursor": event.get("rng_cursor"),
        }
        if expected:
            trace_site = site_map[event["site"]][0]
            result["site"] = trace_site
        else:
            result["site"] = event.get("site")
        return result
    if kind == "first_player":
        return {
            "event": kind,
            "player": event.get("player"),
            "rng_cursor": event.get("rng_cursor"),
        }
    if kind == "draw":
        card = event.get("card", {}) if expected else event
        return {
            "event": "draw",
            "seat": event.get("seat"),
            "instance_id": card.get("instance_id"),
            "template_id": card.get("template_id"),
            "rng_cursor": event.get("rng_cursor"),
        }
    if kind == "shuffle" or (not expected and kind == "reinsert"):
        reinsertion = (
            event.get("kind") == "random_reinsert" if expected else kind == "reinsert"
        )
        if reinsertion:
            return {
                "event": "reinsert",
                "seat": event.get("seat"),
                "template_id": event.get("template_id"),
                "order": event.get("order") if expected else event.get("deck"),
                "rng_cursor": event.get("rng_cursor"),
            }
        order = event.get("order")
        return {
            "event": "shuffle",
            "seat": event.get("seat"),
            "order": order,
            "rng_cursor": event.get("rng_cursor"),
        }
    if kind == "chance":
        view: dict[str, Any] = {
            "event": "chance",
            "name": _chance_name(event.get("name")),
            "seat": event.get("seat"),
            "candidates": _ids(event.get("candidates")),
            "rng_cursor": event.get("rng_cursor"),
        }
        if "target_seat" in event:
            view["target_seat"] = event["target_seat"]
        if expected:
            view["selected"] = _ids(event.get("selected"))
        elif "selected" in event:
            view["selected"] = _ids(event["selected"])
        elif "cards" in event:
            view["selected"] = _ids(event["cards"])
        return view
    raise RecordingError(f"unsupported trace record {kind!r}")


def _trace_mismatch(
    index: int,
    label: str,
    expected: Any,
    actual: Any,
    *,
    rng_cursor: int,
    recent_trace: list[Any],
) -> ReplayDivergence:
    return ReplayDivergence(
        index,
        label,
        expected,
        actual,
        rng_cursor=rng_cursor,
        chance_trace={"recent": recent_trace, "recorded": expected, "native": actual},
    )


def _compare_trace_batch(
    result: dict[str, Any],
    expected_trace: list[tuple[int, dict[str, Any]]],
    trace_cursor: int,
    rng_cursor: int,
    event_index: int,
    *,
    site_map: dict[str, tuple[str | None, bool]],
    stock_act: bool = False,
    special_cards: list[Any] | None = None,
    recent_trace: list[Any],
) -> tuple[int, int]:
    actual_trace = result.get("rng_trace")
    if not isinstance(actual_trace, list):
        raise RecordingError(
            f"event {event_index}: native response has no rng_trace list"
        )
    actual_randoms = 0
    actual_index = 0
    while actual_index < len(actual_trace):
        actual = actual_trace[actual_index]
        if trace_cursor >= len(expected_trace):
            raise _trace_mismatch(
                event_index,
                "native trace has an extra event",
                "<end>",
                actual,
                rng_cursor=result.get("rng_cursor", rng_cursor),
                recent_trace=recent_trace,
            )
        expected_index, expected = expected_trace[trace_cursor]
        actual_event = actual.get("event")
        if actual_event == "chance" and actual.get("name") == "enemy_hand_reveal_pick":
            cards = actual.get("cards")
            if not isinstance(cards, list):
                raise _trace_mismatch(
                    expected_index,
                    "enemy reveal trace",
                    expected,
                    actual,
                    rng_cursor=result.get("rng_cursor", rng_cursor),
                    recent_trace=recent_trace,
                )
            candidates = _ids(actual.get("candidates"))
            for selected in cards:
                if trace_cursor >= len(expected_trace):
                    raise _trace_mismatch(
                        expected_index,
                        "enemy reveal trace has extra selections",
                        "<end>",
                        selected,
                        rng_cursor=result.get("rng_cursor", rng_cursor),
                        recent_trace=recent_trace,
                    )
                expected_index, expected = expected_trace[trace_cursor]
                expected_view = _trace_view(
                    expected,
                    expected=True,
                    site_map=site_map,
                    special_cards=special_cards,
                )
                actual_view = {
                    "event": "chance",
                    "name": "enemy_hand_reveal_pick",
                    "seat": actual.get("seat"),
                    "candidates": candidates,
                    "selected": selected.get("instance_id")
                    if isinstance(selected, dict)
                    else selected,
                    "rng_cursor": expected_view.get("rng_cursor"),
                }
                # The engine emits one batch record; the recorder emits one record per pick.
                expected_view["rng_cursor"] = actual_view["rng_cursor"]
                path, _, _ = first_difference(expected_view, actual_view)
                if path:
                    raise _trace_mismatch(
                        expected_index,
                        "native trace chance",
                        expected_view,
                        actual_view,
                        rng_cursor=result.get("rng_cursor", rng_cursor),
                        recent_trace=recent_trace,
                    )
                recent_trace.append({"recorded": expected_view, "native": actual_view})
                del recent_trace[:-8]
                trace_cursor += 1
            actual_index += 1
            continue
        if expected.get("event") == "chance" and actual_event != "chance":
            raise _trace_mismatch(
                expected_index,
                "native trace event kind",
                expected.get("event"),
                actual_event,
                rng_cursor=result.get("rng_cursor", rng_cursor),
                recent_trace=recent_trace,
            )
        expected_view = _trace_view(
            expected,
            expected=True,
            site_map=site_map,
            stock_act=stock_act,
            special_cards=special_cards,
        )
        actual_view = _trace_view(
            actual,
            expected=False,
            site_map=site_map,
            stock_act=stock_act,
            special_cards=special_cards,
        )
        if expected.get("event") == "shuffle" and expected.get("kind") is None:
            seat = expected.get("seat")
            if (
                isinstance(special_cards, list)
                and type(seat) is int
                and 0 <= seat < len(special_cards)
                and special_cards[seat] not in (None, -1)
                and actual_view.get("order")
                == expected_view.get("order", []) + [special_cards[seat]]
            ):
                actual_view["order"] = expected_view["order"]
        if actual_event == "random":
            actual_randoms += 1
            expected_site = expected_view.get("site")
            actual_site = actual_view.get("site")
            # The native stock port reports a shared label for its internal random calls.
            if actual_site == "stock_act" and stock_act:
                expected_view["site"] = actual_site
            elif actual_site != expected_site:
                raise _trace_mismatch(
                    expected_index,
                    "native trace random site",
                    expected_view,
                    actual_view,
                    rng_cursor=result.get("rng_cursor", rng_cursor),
                    recent_trace=recent_trace,
                )
        path, _, _ = first_difference(expected_view, actual_view)
        if path:
            raise _trace_mismatch(
                expected_index,
                f"native trace {expected.get('event')}",
                expected_view,
                actual_view,
                rng_cursor=result.get("rng_cursor", rng_cursor),
                recent_trace=recent_trace,
            )
        recent_trace.append({"recorded": expected_view, "native": actual_view})
        del recent_trace[:-8]
        trace_cursor += 1
        actual_index += 1

    reported_cursor = result.get("rng_cursor")
    expected_cursor = rng_cursor + actual_randoms
    if type(reported_cursor) is not int or reported_cursor != expected_cursor:
        raise ReplayDivergence(
            event_index,
            "random cursor",
            expected_cursor,
            reported_cursor,
            rng_cursor=reported_cursor if type(reported_cursor) is int else rng_cursor,
            chance_trace={"recent": recent_trace, "native": actual_trace},
        )
    return trace_cursor, reported_cursor


def _card_identity(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("instance_id")
    return value


def _recorded_action(
    event: dict[str, Any], legal_actions: list[dict[str, Any]]
) -> dict[str, Any] | None:
    kind = event.get("event")
    if kind == "pass" or (kind == "action" and event.get("transaction") is None):
        matches = [action for action in legal_actions if action.get("kind") == "pass"]
    elif kind == "choice":
        name = event.get("choice")
        if name == "handleHandShowClose":
            return None
        value = event.get("value")
        if name == "mulligan_decline":
            matches = [
                action
                for action in legal_actions
                if action.get("kind") == "decline_mulligan"
            ]
        else:
            matches = [
                action
                for action in legal_actions
                if action.get("instance_id") == value
                or action.get("template_id") == value
            ]
            if name == "mulligan":
                matches = [
                    action for action in matches if action.get("kind") == "mulligan"
                ]
            else:
                matches = [
                    action for action in matches if action.get("kind") == "choice"
                ]
    else:
        transaction = _transaction_for_decision(event)
        if transaction is None:
            matches = [
                action for action in legal_actions if action.get("kind") == "pass"
            ]
        else:
            source = transaction.get("source_instance_id")
            target = transaction.get("target_instance_id")
            row = {5: "melee", 4: "ranged", 3: "siege"}.get(
                transaction.get("target_slot_id")
            )
            matches = [
                action
                for action in legal_actions
                if action.get("instance_id") == source
                and (target is None or action.get("target_instance_id") == target)
                and (row is None or action.get("row") == row)
            ]
    if len(matches) != 1:
        raise RecordingError(
            f"recorded {kind} maps to {len(matches)} native legal actions: {event!r}"
        )
    return matches[0]


def _attitude_name(value: Any) -> Any:
    if isinstance(value, str):
        return value.lower()
    return value


def _snapshot_stable_state(
    engine: Any, match_id: str, header: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    response = engine.debug_snapshot(match_id)
    snapshot = response.get("snapshot")
    if not isinstance(snapshot, dict):
        raise RecordingError("native snapshot response has no snapshot object")
    players = snapshot.get("players")
    instances = snapshot.get("instances")
    full_state = snapshot.get("full_state")
    if (
        not isinstance(players, list)
        or len(players) != 2
        or not isinstance(instances, list)
    ):
        raise RecordingError("native full snapshot has malformed players or instances")
    card_definitions = (
        full_state.get("cards") if isinstance(full_state, dict) else header["cards"]
    )
    cards = {
        card["id"]: card
        for card in card_definitions
        if isinstance(card, dict) and "id" in card
    }
    by_instance = {
        item["instance_id"]: item for item in instances if isinstance(item, dict)
    }
    observations = []
    for seat in (0, 1):
        result = engine.observe(match_id, seat)
        observation = result.get("observation")
        if not isinstance(observation, dict):
            raise RecordingError(
                f"native observe response for seat {seat} has no observation"
            )
        observations.append(observation)

    def card_record(
        instance_id: int, seat: int, *, current_power: int | None = None
    ) -> dict[str, Any]:
        instance = by_instance.get(instance_id)
        if instance is None:
            raise RecordingError(
                f"native snapshot references missing instance {instance_id}"
            )
        template_id = instance.get("template_id")
        definition = cards.get(template_id, {})
        return {
            "instance_id": instance_id,
            "template_id": template_id,
            "current_power": definition.get("power", 0)
            if current_power is None
            else current_power,
            "owner": instance.get("owner"),
            "seat": seat,
        }

    stable_players = []
    for seat, player in enumerate(players):
        observation = observations[seat]
        observed_player = observation.get("players", [])[seat]
        row_power: dict[int, int] = {}
        row_scores = {}
        for row in ("melee", "ranged", "siege"):
            observed_row = observed_player.get("rows", {}).get(row, {})
            row_scores[row] = observed_row.get("score", 0)
            for card in observed_row.get("cards", []):
                if isinstance(card, dict) and type(card.get("instance_id")) is int:
                    power = card.get("score")
                    if type(power) is int:
                        row_power[card["instance_id"]] = power

        def records(
            instance_ids: list[int], record_seat: int = seat
        ) -> list[dict[str, Any]]:
            return [
                card_record(
                    instance_id, record_seat, current_power=row_power.get(instance_id)
                )
                for instance_id in instance_ids
            ]

        rows = {
            row: records(player.get("rows", {}).get(row, []))
            for row in ("melee", "ranged", "siege")
        }
        modifier_ids = player.get("modifiers", {})
        modifiers = {
            row: records([modifier_ids[row]])
            if modifier_ids.get(row) is not None
            else []
            for row in ("melee", "ranged", "siege")
        }
        stable_player = {
            "seat": seat,
            "rows": rows,
            "row_scores": row_scores,
            "score": sum(row_scores.values()),
            "lives": snapshot.get("lives", [None, None])[seat],
            "hand_count": len(player.get("hand", [])),
            "graveyard": records(player.get("graveyard", [])),
            "leader": records([player["leader"]])
            if player.get("leader") is not None
            else [],
            "deck_count": len(player.get("deck", [])),
            "passed": player.get("passed"),
            "modifiers": modifiers,
        }
        if header["seat_kinds"][seat] == "stock_ai":
            stable_player["ai_hand"] = records(player.get("hand", []))
        stable_players.append(stable_player)

    stable = {
        "round": snapshot.get("round"),
        "actor": snapshot.get("to_move"),
        "players": stable_players,
        "weather": [
            card_record(instance_id, -1) for instance_id in snapshot.get("weather", [])
        ],
    }
    return stable, snapshot


def _without_list_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_list_fields(item)
            for key, item in value.items()
            if key != "list"
        }
    if isinstance(value, list):
        return [_without_list_fields(item) for item in value]
    return value


def _choice_is_automatic(
    event: dict[str, Any], events: list[dict[str, Any]], index: int
) -> bool:
    if event.get("choice") == "handleHandShowClose":
        return True
    if (
        event.get("choice") not in {"handleNurseChoice", "handleResurrectChoice"}
        or index == 0
    ):
        return False
    previous = events[index - 1]
    return previous.get("event") == "chance" and _card_identity(
        previous.get("selected")
    ) == event.get("value")


def replay_recording(
    events: list[dict[str, Any]],
    engine: Any,
    *,
    from_snapshot: dict[str, Any] | None = None,
    from_event: int | None = None,
) -> dict[str, int]:
    """Replay one recording through the native engine's documented protocol."""
    validate_recording(events)
    if any(
        event.get("event") == "first_player" and event.get("player") == -1
        for event in events
    ):
        raise RecordingError(
            "recording does not contain the selected first player for the player-side Scoia'tael choice"
        )
    if (from_snapshot is None) != (from_event is None):
        raise RecordingError(
            "--from-snapshot and --from-event must be supplied together"
        )
    if from_event is not None:
        if type(from_event) is not int or not 0 <= from_event < len(events):
            raise RecordingError("--from-event is outside the recording")
        if events[from_event].get("event") not in {"match_start", "state"}:
            raise RecordingError(
                "--from-event must point to match_start or a stable state record"
            )

    header = events[0]
    random_events = _random_events(events)
    random_script = [event["value"] for event in random_events]
    site_map = _engine_site_map(engine, random_events)
    expected_trace = _trace_events(events)
    special_cards = header["special_cards"]
    recent_trace: list[Any] = []
    counts = {"random": 0, "decisions": 0, "actions": 0, "choices": 0, "states": 0}
    pending_stock: dict[str, Any] | None = None
    active_action_id: int | None = None
    match_id: str | None = None
    trace_cursor = 0

    if from_snapshot is None:
        created = engine.new_match(
            header["cards"],
            _decks(header),
            seed=header.get("seed", 0),
            leaders=header["leaders"],
            factions=header["factions"],
            seat_kind=[
                "ai" if seat == "stock_ai" else seat for seat in header["seat_kinds"]
            ],
            rng_script=random_script,
        )
        match_id = _match_id(created)
        trace_cursor, rng_cursor = _compare_trace_batch(
            created,
            expected_trace,
            trace_cursor,
            0,
            0,
            site_map=site_map,
            special_cards=special_cards,
            recent_trace=recent_trace,
        )
        first_event = 1
        starting_rng_cursor = 0
        pending_choice = created.get("observation", {}).get("pending_choice")
        pending_coin_toss = (
            isinstance(pending_choice, dict)
            and pending_choice.get("kind") == "coin_toss"
        )
        coin_toss_seat = pending_choice.get("seat") if pending_coin_toss else None
    else:
        assert from_event is not None
        created = engine.load_state(from_snapshot)
        match_id = _match_id(created)
        rng_cursor = created.get("rng_cursor")
        expected_rng_cursor = sum(
            event.get("event") == "random" for event in events[: from_event + 1]
        )
        if type(rng_cursor) is not int or rng_cursor != expected_rng_cursor:
            raise RecordingError(
                f"native snapshot rng_cursor {rng_cursor!r} differs from recording prefix {expected_rng_cursor}"
            )
        trace_cursor = sum(index <= from_event for index, _ in expected_trace)
        first_event = from_event + 1
        starting_rng_cursor = rng_cursor
        pending_coin_toss = False
        coin_toss_seat = None

    try:
        for index, event in enumerate(events[first_event:], start=first_event):
            kind = event["event"]
            if kind in {"action", "pass", "choice"}:
                expected_cursor = event.get("rng_cursor")
                if expected_cursor is not None and expected_cursor != rng_cursor:
                    raise ReplayDivergence(
                        index,
                        "random cursor",
                        expected_cursor,
                        rng_cursor,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    )
            if kind == "stock_decision":
                if pending_stock is not None:
                    raise RecordingError(
                        f"event {index}: stock decision arrived before the prior action"
                    )
                seat = event.get("seat")
                legal = engine.legal_actions(match_id, seat)
                actions = legal.get("legal_actions")
                if not isinstance(actions, list):
                    raise RecordingError(
                        f"event {index}: native legal response has no actions"
                    )
                try:
                    expected_action = _recorded_action(event, actions)
                except RecordingError as exc:
                    raise ReplayDivergence(
                        index,
                        "stock decision action mapping",
                        event.get("transaction"),
                        actions,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    ) from exc
                result = engine.request("stock_act", match_id=match_id, seat=seat)
                trace_cursor, rng_cursor = _compare_trace_batch(
                    result,
                    expected_trace,
                    trace_cursor,
                    rng_cursor,
                    index,
                    site_map=site_map,
                    stock_act=True,
                    special_cards=special_cards,
                    recent_trace=recent_trace,
                )
                actual_action = result.get("action")
                actual_attitude = result.get("attitude")
                expected_name = _attitude_name(event.get("attitude_name"))
                actual_decision = {
                    "seat": result.get("seat", seat),
                    "attitude_name": actual_attitude,
                    "action": actual_action,
                }
                expected_decision = {
                    "seat": seat,
                    "attitude_name": expected_name,
                    "action": expected_action,
                }
                path, _, _ = first_difference(expected_decision, actual_decision)
                if path:
                    raise ReplayDivergence(
                        index,
                        "stock decision",
                        expected_decision,
                        actual_decision,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    )
                pending_stock = {
                    "seat": seat,
                    "transaction": event.get("transaction"),
                    "action": actual_action,
                }
                counts["decisions"] += 1
            elif kind == "action":
                seat = event.get("seat")
                if pending_stock is not None:
                    expected = {
                        "seat": pending_stock["seat"],
                        "transaction": pending_stock["transaction"],
                    }
                    actual = {"seat": seat, "transaction": event.get("transaction")}
                    path, _, _ = first_difference(expected, actual)
                    if path:
                        raise ReplayDivergence(
                            index,
                            "applied stock action",
                            expected,
                            actual,
                            rng_cursor=rng_cursor,
                            chance_trace={"recent": recent_trace},
                        )
                    action = pending_stock["action"]
                    pending_stock = None
                else:
                    legal = engine.legal_actions(match_id, seat)
                    actions = legal.get("legal_actions")
                    if not isinstance(actions, list):
                        raise RecordingError(
                            f"event {index}: native legal response has no actions"
                        )
                    try:
                        action = _recorded_action(event, actions)
                    except RecordingError as exc:
                        raise ReplayDivergence(
                            index,
                            "recorded action mapping",
                            event.get("transaction"),
                            actions,
                            rng_cursor=rng_cursor,
                            chance_trace={"recent": recent_trace},
                        ) from exc
                if action is None:
                    raise RecordingError(f"event {index}: action has no native action")
                legal = engine.legal_actions(match_id, seat)
                result = engine.step(
                    match_id, seat, action, decision_id=legal["decision_id"]
                )
                trace_cursor, rng_cursor = _compare_trace_batch(
                    result,
                    expected_trace,
                    trace_cursor,
                    rng_cursor,
                    index,
                    site_map=site_map,
                    special_cards=special_cards,
                    recent_trace=recent_trace,
                )
                active_action_id = event.get("action_id")
                counts["actions"] += 1
            elif kind == "pass":
                seat = event.get("seat")
                if pending_stock is not None:
                    if (
                        pending_stock["seat"] != seat
                        or pending_stock["action"].get("kind") != "pass"
                    ):
                        raise ReplayDivergence(
                            index,
                            "stock pass",
                            pending_stock,
                            event,
                            rng_cursor=rng_cursor,
                            chance_trace={"recent": recent_trace},
                        )
                    action = pending_stock["action"]
                    pending_stock = None
                else:
                    legal = engine.legal_actions(match_id, seat)
                    actions = legal.get("legal_actions")
                    if not isinstance(actions, list):
                        raise RecordingError(
                            f"event {index}: native legal response has no actions"
                        )
                    try:
                        action = _recorded_action(event, actions)
                    except RecordingError as exc:
                        raise ReplayDivergence(
                            index,
                            "recorded pass mapping",
                            event,
                            actions,
                            rng_cursor=rng_cursor,
                            chance_trace={"recent": recent_trace},
                        ) from exc
                if action.get("kind") != "pass":
                    raise ReplayDivergence(
                        index, "recorded pass", "pass", action, rng_cursor=rng_cursor
                    )
                legal = engine.legal_actions(match_id, seat)
                result = engine.step(
                    match_id, seat, action, decision_id=legal["decision_id"]
                )
                trace_cursor, rng_cursor = _compare_trace_batch(
                    result,
                    expected_trace,
                    trace_cursor,
                    rng_cursor,
                    index,
                    site_map=site_map,
                    special_cards=special_cards,
                    recent_trace=recent_trace,
                )
                action = None
                counts["actions"] += 1
            elif kind == "choice":
                if _choice_is_automatic(event, events, index):
                    counts["choices"] += 1
                    continue
                seat = event.get("seat")
                legal = engine.legal_actions(match_id, seat)
                actions = legal.get("legal_actions")
                if not isinstance(actions, list):
                    raise RecordingError(
                        f"event {index}: native legal response has no actions"
                    )
                if not any(
                    action.get("kind") in {"choice", "mulligan", "decline_mulligan"}
                    for action in actions
                ):
                    raise ReplayDivergence(
                        index,
                        "recorded choice unavailable",
                        event,
                        actions,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    )
                try:
                    action = _recorded_action(event, actions)
                except RecordingError as exc:
                    raise ReplayDivergence(
                        index,
                        "recorded choice mapping",
                        event,
                        actions,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    ) from exc
                if action is not None:
                    result = engine.step(
                        match_id, seat, action, decision_id=legal["decision_id"]
                    )
                    trace_cursor, rng_cursor = _compare_trace_batch(
                        result,
                        expected_trace,
                        trace_cursor,
                        rng_cursor,
                        index,
                        site_map=site_map,
                        special_cards=special_cards,
                        recent_trace=recent_trace,
                    )
                counts["choices"] += 1
            elif kind == "first_player" and pending_coin_toss:
                expected_cursor = event.get("rng_cursor")
                if expected_cursor is not None and expected_cursor != rng_cursor:
                    raise ReplayDivergence(
                        index,
                        "random cursor",
                        expected_cursor,
                        rng_cursor,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    )
                selected_player = event.get("player")
                legal = engine.legal_actions(match_id, coin_toss_seat)
                actions = legal.get("legal_actions")
                if not isinstance(actions, list):
                    raise RecordingError(
                        f"event {index}: native legal response has no coin-toss actions"
                    )
                matches = [
                    action
                    for action in actions
                    if action.get("template_id") == selected_player
                ]
                if len(matches) != 1:
                    raise ReplayDivergence(
                        index,
                        "first-player choice",
                        selected_player,
                        actions,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    )
                result = engine.step(
                    match_id,
                    coin_toss_seat,
                    matches[0],
                    decision_id=legal["decision_id"],
                )
                trace_cursor, rng_cursor = _compare_trace_batch(
                    result,
                    expected_trace,
                    trace_cursor,
                    rng_cursor,
                    index,
                    site_map=site_map,
                    special_cards=special_cards,
                    recent_trace=recent_trace,
                )
                pending_coin_toss = False
                counts["choices"] += 1
            elif kind == "state":
                action_id = event.get("action_id")
                if active_action_id is None or action_id != active_action_id:
                    raise ReplayDivergence(
                        index,
                        "action sequence",
                        active_action_id,
                        action_id,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    )
                actual, native_snapshot = _snapshot_stable_state(
                    engine, match_id, header
                )
                expected = _without_list_fields(event["state"])
                path, _, _ = first_difference(expected, actual)
                if path:
                    raise ReplayDivergence(
                        index,
                        "stable state",
                        expected,
                        actual,
                        rng_cursor=native_snapshot.get("rng_cursor", rng_cursor),
                        chance_trace={"recent": recent_trace},
                        native_snapshot=native_snapshot,
                        resume_after_event=index,
                    )
                active_action_id = None
                counts["states"] += 1
            elif kind == "match_end":
                if pending_stock is not None or active_action_id is not None:
                    raise RecordingError(
                        f"event {index}: recording ended with an unresolved action"
                    )
            elif kind == "chance" and event.get("name") == "agile_tie_pick":
                random_event = events[index - 1] if index else {}
                value = (
                    random_event.get("value")
                    if random_event.get("event") == "random"
                    else None
                )
                selected = (
                    "ranged"
                    if isinstance(value, (int, float)) and value < 0.5
                    else "melee"
                )
                expected = {
                    "site": random_event.get("site"),
                    "value": value,
                    "selected": selected,
                }
                actual = {
                    "site": random_event.get("site"),
                    "value": event.get("value"),
                    "selected": event.get("selected"),
                }
                path, _, _ = first_difference(expected, actual)
                if path:
                    raise ReplayDivergence(
                        index,
                        "agile tie chance",
                        expected,
                        actual,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    )

            # Trace records can precede the stock_decision/action that caused them.
            # Their cursor is checked against the operation's returned rng_trace.
            if kind in {"stock_decision", "state", "match_end"}:
                expected_cursor = event.get("rng_cursor")
                if expected_cursor is not None and expected_cursor != rng_cursor:
                    raise ReplayDivergence(
                        index,
                        "random cursor",
                        expected_cursor,
                        rng_cursor,
                        rng_cursor=rng_cursor,
                        chance_trace={"recent": recent_trace},
                    )

        if pending_stock is not None or active_action_id is not None:
            raise RecordingError("recording ended with an unresolved native action")
        if trace_cursor != len(expected_trace):
            event_index, expected = expected_trace[trace_cursor]
            raise _trace_mismatch(
                event_index,
                "native trace ended early",
                expected,
                "<missing>",
                rng_cursor=rng_cursor,
                recent_trace=recent_trace,
            )
        if rng_cursor != len(random_script):
            raise ReplayDivergence(
                len(events) - 1,
                "final random cursor",
                len(random_script),
                rng_cursor,
                rng_cursor=rng_cursor,
                chance_trace={"recent": recent_trace},
            )
        counts["random"] = rng_cursor - starting_rng_cursor
        return counts
    finally:
        if match_id is not None:
            try:
                engine.close_match(match_id)
            except Exception:
                pass


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON number {value}")
