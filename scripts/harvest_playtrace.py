#!/usr/bin/env python3
"""Read public live play-by-play chunks; preserve complete and partial matches.

This is a diagnostic timeline, not an exact engine replay: random draws and
hidden card identities are deliberately absent.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.mod_trace import SCHEMA, SETTINGS_SECTION

DEFAULT_OUTPUT = ROOT / ".runtime/playtraces"
MATCH_KEY = re.compile(r"^(t\d{9})_(\d{9}|count|status|build|error)$")
BUILD_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
STATUSES = {"active", "final", "interrupted", "error", "truncated"}
EVENT_KEYS = {
    "match_start": {
        "schema",
        "schema_version",
        "build",
        "information",
        "state",
        "selection",
    },
    "action_started": {"seat", "action_id"},
    "action": {"seat", "action_id", "transaction", "state"},
    "action_cancelled": {"seat", "action_id", "state"},
    "pass": {"seat"},
    "round_complete": {"seat", "reason"},
    "choice": {"seat", "choice"},
    "decision_source": {"source", "error", "stage", "selection"},
    "round_start": {"state"},
    "round_end": {"state"},
    "match_end": {"reason", "winner", "pending_action", "state"},
    "trace_error": set(),
}


class PlayTraceError(ValueError):
    """Missing, corrupt or mismatched public trace evidence."""


def _native_settings_values(path: Path, match_id: str | None = None) -> dict[str, str]:
    """Read native INI framing; quotes delimit raw strings, not JSON strings.

    The game writes inner JSON quotes/backslashes verbatim and renders record
    newlines as literal backslash+n. Strip only the outer native delimiters;
    interpreting escapes here would corrupt strings split across chunks.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PlayTraceError(f"cannot read public trace settings: {exc}") from exc
    values: dict[str, str] = {}
    inside = False
    for line_number, line in enumerate(text.split("\n"), 1):
        line = line.removesuffix("\r")
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == f"[{SETTINGS_SECTION}]"
            continue
        if not inside or not stripped or stripped.startswith((";", "#")):
            continue
        key, separator, raw_field = line.partition("=")
        key = key.strip()
        if match_id is not None and not key.startswith(match_id + "_"):
            continue
        raw = raw_field.strip()
        if not separator or not key:
            raise PlayTraceError(f"{path}:{line_number}: malformed native setting")
        if key in values:
            raise PlayTraceError(f"{path}:{line_number}: duplicate trace key {key!r}")
        match = MATCH_KEY.fullmatch(key)
        is_chunk = match is not None and match[2].isdecimal()
        if raw.startswith('"'):
            if len(raw) < 2 or not raw.endswith('"'):
                raise PlayTraceError(
                    f"{path}:{line_number}: unterminated native setting string"
                )
            values[key] = raw[1:-1]
        else:
            if '"' in raw:
                raise PlayTraceError(
                    f"{path}:{line_number}: malformed bare native fragment"
                )
            if is_chunk and (raw_field != raw or re.search(r"\s", raw)):
                raise PlayTraceError(
                    f"{path}:{line_number}: bare chunk must not contain whitespace"
                )
            values[key] = raw
    if not values:
        raise PlayTraceError(f"settings file has no [{SETTINGS_SECTION}] trace section")
    return values


def _reject_constant(value: str) -> None:
    raise ValueError(f"unsupported numeric constant {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate event field {key!r}")
        result[key] = value
    return result


def _native_events(body: str, identifier: str) -> tuple[list[dict[str, Any]], str]:
    """Decode JSON values before consuming their native literal ``\\n`` separator."""
    decoder = json.JSONDecoder(
        parse_constant=_reject_constant, object_pairs_hook=_unique_object
    )
    cursor = 0
    events = []
    records = []
    while cursor < len(body):
        try:
            event, end = decoder.raw_decode(body, cursor)
        except (ValueError, json.JSONDecodeError) as exc:
            raise PlayTraceError(
                f"{identifier}: invalid or partial JSON event at character {cursor}"
            ) from exc
        if not isinstance(event, dict):
            raise PlayTraceError(f"{identifier}: JSON event is not an object")
        if body[end : end + 2] != r"\n":
            raise PlayTraceError(
                f"{identifier}: missing native record separator at character {end}"
            )
        events.append(event)
        # Preserve the validated raw JSON bytes; normalize only the delimiter.
        records.append(body[cursor:end] + "\n")
        cursor = end + 2
    return events, "".join(records)


def _keys(value: Any, allowed: set[str], where: str) -> None:
    if not isinstance(value, dict) or set(value) - allowed:
        raise PlayTraceError(
            f"{where}: unexpected fields; refuses non-public or unsupported data"
        )


def _integer(value: Any, where: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise PlayTraceError(f"{where}: invalid integer")


def _selection(selection: Any) -> None:
    fields = {
        "enabled",
        "requested_tier",
        "effective_tier",
        "mode",
        "opponent",
        "npc_difficulty",
        "checkpoint_sha256",
        "weights_sha256",
    }
    _keys(selection, fields, "AI selection")
    if set(selection) != fields or type(selection["enabled"]) is not bool:
        raise PlayTraceError("AI selection is incomplete")
    for key in ("requested_tier", "npc_difficulty"):
        if type(selection[key]) is not int or selection[key] not in (1, 2, 3):
            raise PlayTraceError("AI selection has an invalid requested difficulty")
    tier = selection["effective_tier"]
    if type(tier) is not int or tier not in (
        (1, 2, 3) if selection["enabled"] else (0,)
    ):
        raise PlayTraceError("AI selection has an invalid effective tier")
    if selection["mode"] not in {"story", "practice"}:
        raise PlayTraceError("AI selection has an invalid match mode")
    if not isinstance(selection["opponent"], str) or not re.fullmatch(
        r"[A-Za-z0-9_]{0,128}", selection["opponent"]
    ):
        raise PlayTraceError("AI selection has an invalid opponent identifier")
    for key in ("checkpoint_sha256", "weights_sha256"):
        expected = r"[0-9a-f]{64}" if selection["enabled"] else ""
        if (
            not isinstance(selection[key], str)
            or re.fullmatch(expected, selection[key]) is None
        ):
            raise PlayTraceError("AI selection has an invalid policy identity")


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _card(card: Any) -> None:
    fields = {"instance_id", "template_id", "name", "power", "owner", "seat"}
    _keys(card, fields, "public card")
    if set(card) != fields:
        raise PlayTraceError("public card: incomplete identity")
    for field in ("instance_id", "template_id", "owner", "seat"):
        _integer(card[field], f"card.{field}", -1)
    if not isinstance(card["name"], str) or not _number(card["power"]):
        raise PlayTraceError("public card: invalid label or power")


def _cards(cards: Any) -> None:
    if not isinstance(cards, list):
        raise PlayTraceError("public card list is not an array")
    for card in cards:
        _card(card)


def _state(state: Any) -> None:
    fields = {"round", "actor", "players", "round_results", "weather"}
    _keys(state, fields, "public state")
    if set(state) != fields:
        raise PlayTraceError("public state: incomplete snapshot")
    _integer(state["round"], "state.round", 1)
    _integer(state["actor"], "state.actor", -1)
    players = state["players"]
    if not isinstance(players, list) or len(players) != 2:
        raise PlayTraceError("public state needs two players")
    for seat, player in enumerate(players):
        fields = {
            "seat",
            "score",
            "hand_count",
            "deck_count",
            "passed",
            "rows",
            "graveyard",
            "leader",
            "modifiers",
        }
        _keys(player, fields, "public player")
        if (
            set(player) != fields
            or type(player["seat"]) is not int
            or player["seat"] != seat
        ):
            raise PlayTraceError("public player: incomplete or wrong seat")
        for field in ("hand_count", "deck_count"):
            _integer(player[field], f"player.{field}")
        if not _number(player["score"]) or type(player["passed"]) is not bool:
            raise PlayTraceError("public player: invalid score or pass state")
        for field in ("rows", "modifiers"):
            _keys(player[field], {"melee", "ranged", "siege"}, f"player.{field}")
            if set(player[field]) != {"melee", "ranged", "siege"}:
                raise PlayTraceError("public player: incomplete rows")
            for cards in player[field].values():
                _cards(cards)
        _cards(player["graveyard"])
        _cards(player["leader"])
    _cards(state["weather"])
    if not isinstance(state["round_results"], list):
        raise PlayTraceError("round results must be an array")
    for result in state["round_results"]:
        _keys(result, {"winner", "scores"}, "round result")
        if (
            set(result) != {"winner", "scores"}
            or type(result["winner"]) is not int
            or result["winner"] not in (-1, 0, 1)
        ):
            raise PlayTraceError("invalid round result")
        scores = result["scores"]
        if (
            not isinstance(scores, list)
            or len(scores) != 2
            or any(not _number(x) for x in scores)
        ):
            raise PlayTraceError("invalid round scores")


def validate_events(events: list[dict[str, Any]], build: str, status: str) -> None:
    if (
        not events
        or not isinstance(events[0], dict)
        or events[0].get("event") != "match_start"
    ):
        raise PlayTraceError("trace is missing its match_start header")
    header = events[0]
    if (
        header.get("schema"),
        header.get("schema_version"),
        header.get("information"),
    ) != (SCHEMA, 1, "public"):
        raise PlayTraceError("unsupported or non-public trace schema")
    if header.get("build") != build:
        raise PlayTraceError("header build differs from the persisted build identity")
    pending: int | None = None
    pending_seat: int | None = None
    next_action = 1
    ended = False
    for index, event in enumerate(events):
        if (
            not isinstance(event, dict)
            or event.get("seq") != index
            or type(event.get("seq")) is not int
        ):
            raise PlayTraceError(f"event {index}: sequence gap or duplicate")
        kind = event.get("event")
        if not isinstance(kind, str) or kind not in EVENT_KEYS:
            raise PlayTraceError(f"event {index}: unsupported event kind")
        _keys(event, EVENT_KEYS[kind] | {"event", "seq"}, f"event {index}")
        optional = {"selection"} if kind == "match_start" else set()
        if kind == "decision_source":
            optional = {"error", "stage", "selection"}
        required = EVENT_KEYS[kind] - optional
        if not required.issubset(event):
            raise PlayTraceError(f"event {index}: missing required fields")
        if ended:
            raise PlayTraceError("event appears after match_end")
        if kind == "match_start" and index != 0:
            raise PlayTraceError("trace contains more than one match")
        if "state" in event:
            _state(event["state"])
        if "selection" in event:
            _selection(event["selection"])
        if "seat" in event and (
            type(event["seat"]) is not int or event["seat"] not in (0, 1)
        ):
            raise PlayTraceError(f"event {index}: invalid seat")
        if "action_id" in event:
            _integer(event["action_id"], f"event {index}.action_id", 1)
        if kind == "action_started":
            if pending is not None or event.get("action_id") != next_action:
                raise PlayTraceError(
                    "native action start is missing, duplicated or out of order"
                )
            pending = next_action
            pending_seat = event["seat"]
            next_action += 1
        if kind in {"action", "action_cancelled"}:
            if (
                pending is None
                or event.get("action_id") != pending
                or event["seat"] != pending_seat
            ):
                raise PlayTraceError(
                    "applied action has no matching native action start"
                )
            pending = None
            if kind == "action":
                transaction = event.get("transaction")
                _keys(
                    transaction,
                    {"source", "target", "target_slot", "target_seat"},
                    "public transaction",
                )
                if set(transaction) != {
                    "source",
                    "target",
                    "target_slot",
                    "target_seat",
                }:
                    raise PlayTraceError("public transaction is incomplete")
                _card(transaction["source"])
                if transaction["target"] is not None:
                    _card(transaction["target"])
                _integer(transaction["target_slot"], "transaction.target_slot", -1)
                _integer(transaction["target_seat"], "transaction.target_seat", -1)
        if kind == "decision_source":
            if event.get("source") not in {"network", "stock", "vanilla", "greedy"}:
                raise PlayTraceError("unknown decision source")
            if (
                "selection" in event
                and event["source"] in {"network", "greedy"}
                and not event["selection"]["enabled"]
            ):
                raise PlayTraceError("network source contradicts disabled selection")
            for field in ("error", "stage"):
                if field in event and not isinstance(event[field], str):
                    raise PlayTraceError("invalid diagnostic label")
        if kind == "choice" and not isinstance(event.get("choice"), str):
            raise PlayTraceError("invalid public choice label")
        if kind == "round_complete" and event["reason"] != "no_cards":
            raise PlayTraceError("invalid forced round completion reason")
        if kind == "match_end":
            ended = True
            if (
                event.get("reason") not in {"final", "interrupted"}
                or type(event.get("winner")) is not int
                or event.get("winner") not in (-1, 0, 1)
            ):
                raise PlayTraceError("invalid match ending")
            if event.get("pending_action") is not (pending is not None):
                raise PlayTraceError(
                    "match end does not describe its pending native action"
                )
            if event["reason"] == "final" and pending is not None:
                raise PlayTraceError("final match has an unfinished native action")
    if status == "final" and (not ended or events[-1].get("reason") != "final"):
        raise PlayTraceError("final status has no final match_end record")
    if status in {"active", "truncated", "error"} and ended:
        raise PlayTraceError("unfinished status conflicts with a match_end record")


def read_traces(
    settings: Path, expected_build: str | None = None, match_id: str | None = None
) -> dict[str, dict[str, Any]]:
    values = _native_settings_values(settings, match_id)
    groups: dict[str, dict[str, str]] = {}
    for key, value in values.items():
        if key in {"next_match", "active_match"}:
            continue
        match = MATCH_KEY.fullmatch(key)
        if match is None:
            raise PlayTraceError(f"unsupported play trace key {key!r}")
        groups.setdefault(match[1], {})[match[2]] = value
    traces = {}
    if match_id is not None:
        if match_id not in groups:
            raise PlayTraceError(f"no persisted trace {match_id}")
        groups = {match_id: groups[match_id]}
    for identifier, data in sorted(groups.items()):
        build = data.get("build", "")
        if BUILD_ID.fullmatch(build) is None or (
            expected_build is not None and build != expected_build
        ):
            raise PlayTraceError(f"{identifier}: missing or unexpected build identity")
        status = data.get("status", "")
        if status not in STATUSES:
            raise PlayTraceError(f"{identifier}: missing or invalid status")
        if not data.get("count", "").isdecimal():
            raise PlayTraceError(f"{identifier}: missing or invalid chunk count")
        count = int(data["count"])
        error = data.get("error", "")
        if error and (status != "error" or len(error) > 160):
            raise PlayTraceError(f"{identifier}: invalid plain-string failure record")
        chunks = {int(key): value for key, value in data.items() if key.isdecimal()}
        if count > 20000 or set(chunks) != set(range(count)):
            raise PlayTraceError(
                f"{identifier}: chunk gap, truncation or unexpected extra chunk"
            )
        body = "".join(chunks[index] for index in range(count))
        if any(not chunk or len(chunk) > 780 for chunk in chunks.values()):
            raise PlayTraceError(f"{identifier}: invalid native chunk size")
        if not body and status != "final":
            traces[identifier] = {
                "build": build,
                "status": status,
                "error": error,
                "events": [],
                "jsonl": "",
                "chunks": 0,
            }
            continue
        events, jsonl = _native_events(body, identifier)
        validate_events(events, build, status)
        traces[identifier] = {
            "build": build,
            "status": status,
            "events": events,
            "jsonl": jsonl,
            "chunks": count,
            "error": error,
        }
    if not traces:
        raise PlayTraceError("no public play traces have been flushed")
    return traces


def timeline(match_id: str, trace: dict[str, Any]) -> str:
    lines = [
        f"{match_id}: {trace['status']} | build {trace['build']} | public information only"
    ]
    if trace.get("error"):
        lines.append(f"Trace failure: {trace['error']}")
    if not trace["events"]:
        lines.append(
            "No public events were persisted; metadata alone does not prove tracing worked."
        )
    for event in trace["events"]:
        kind = event["event"]
        seat = "Player" if event.get("seat") == 0 else "Opponent"
        text = kind.replace("_", " ")
        if kind == "pass":
            text = f"{seat} passed"
        elif kind == "round_complete":
            text = f"{seat} finished the round: no cards or usable leader"
        elif kind == "action_started":
            text = f"{seat}: native action {event['action_id']} started (not yet confirmed)"
        elif kind == "action":
            transaction = event["transaction"]
            card = transaction["source"]
            text = f"{seat} played {card['name']} [template {card['template_id']}, instance {card['instance_id']}] to slot {transaction['target_slot']} / seat {transaction['target_seat']}"
            if transaction["target"]:
                target = transaction["target"]
                text += (
                    f" targeting {target['name']} [instance {target['instance_id']}]"
                )
        elif kind == "choice":
            text = f"{seat}: {event['choice']} (hidden selection omitted)"
        elif kind == "decision_source":
            text = f"AI source: {event['source']}"
            if selection := event.get("selection"):
                labels = {0: "Stock", 1: "Easy", 2: "Medium", 3: "Hard"}
                text += (
                    f" | {selection['mode']}, opponent {selection['opponent'] or 'unspecified'}"
                    f", requested {labels[selection['requested_tier']]}"
                    f", effective {labels[selection['effective_tier']]}"
                )
                if selection["checkpoint_sha256"]:
                    text += f", model {selection['checkpoint_sha256'][:12]}"
            if "error" in event:
                text += f" | {event.get('stage', 'policy')}: {event['error']}"
        elif kind == "match_end":
            text = f"Match ended: {event['reason']}, winner seat {event['winner']}"
        state = event.get("state")
        if state:
            scores = [p["score"] for p in state["players"]]
            hands = [p["hand_count"] for p in state["players"]]
            text += f" | round {state['round']}, scores {scores[0]}–{scores[1]}, cards in hand {hands[0]}–{hands[1]}"
        lines.append(f"{event['seq']:04}: {text}")
    if trace["status"] != "final":
        lines.append(
            "Partial trace: no completed-match claim. Events shown are the persisted prefix."
        )
    return "\n".join(lines) + "\n"


def _write_once(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text() != content:
            raise PlayTraceError(
                f"refusing to overwrite different trace evidence: {path}"
            )
        return
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)


def harvest(
    settings: Path,
    output_dir: Path = DEFAULT_OUTPUT,
    match_id: str | None = None,
    expected_build: str | None = None,
) -> list[Path]:
    traces = read_traces(settings, match_id=match_id)
    if expected_build is not None:
        if match_id is not None and traces[match_id]["build"] != expected_build:
            raise PlayTraceError(f"{match_id}: unexpected build identity")
        traces = {
            identifier: trace
            for identifier, trace in traces.items()
            if trace["build"] == expected_build
        }
        if not traces:
            raise PlayTraceError(f"no persisted trace from build {expected_build}")
    outputs = []
    for identifier, trace in traces.items():
        directory = output_dir / identifier
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{trace['status']}-{len(trace['events']):09}"
        target = directory / f"{stem}.jsonl"
        _write_once(target, trace["jsonl"])
        rendered = timeline(identifier, trace)
        _write_once(directory / f"{stem}.txt", rendered)
        print(rendered, end="")
        outputs.append(target)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings_file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--match")
    parser.add_argument("--expect-build")
    args = parser.parse_args()
    try:
        harvest(args.settings_file, args.output_dir, args.match, args.expect_build)
    except (PlayTraceError, OSError) as exc:
        print(f"play trace: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
