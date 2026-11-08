#!/usr/bin/env python3
"""Collect a completed diagnostic build's private actor-view records for offline replay."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.harvest_playtrace import PlayTraceError, _native_events
from scripts.mod_diagnostic import SCHEMA

SECTION = "W3GwentPolicyDiagnostic"
POLICY_FIELDS = {
    "event",
    "seq",
    "decision_index",
    "seat",
    "native_decision_id",
    "difficulty",
    "temperature",
    "random_draws",
    "observation_profile_id",
    "checkpoint_sha256",
    "weights_sha256",
    "observation",
    "legal_actions",
    "encoded_observation",
    "selected_action",
    "selected_action_id",
    "selected_index",
    "group_logits",
    "local_logits",
    "action_group_ids",
    "action_features",
    "group_features",
    "value",
    "value_nonfinite",
    "outcome_probabilities",
}
FAILURE_FIELDS = {
    "event",
    "seq",
    "decision_index",
    "seat",
    "native_decision_id",
    "difficulty",
    "temperature",
    "random_draws",
    "observation_profile_id",
    "checkpoint_sha256",
    "weights_sha256",
    "observation",
    "legal_actions",
    "error_category",
}
OBS_FIELDS = {
    "seat",
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
PLAYER_FIELDS = {
    "seat",
    "faction",
    "hand_count",
    "deck_count",
    "leader",
    "leader_used",
    "graveyard",
    "rows",
    "modifiers",
    "passed",
}


def descriptor_from_manifest(manifest: dict) -> dict:
    if (
        manifest.get("format") != "witcher3-gwent-mod-build-v4"
        or manifest.get("variant") != "diagnostic"
        or manifest.get("status") != "offline_compiled_and_export_verified"
    ):
        raise PlayTraceError("verified diagnostic build manifest required")
    descriptor = manifest.get("policy_diagnostic", {})
    identity = descriptor.get("build_identity")
    if (
        descriptor.get("schema") != SCHEMA
        or not isinstance(identity, str)
        or re.fullmatch(r"[a-f0-9]{64}", identity) is None
    ):
        raise PlayTraceError("invalid diagnostic build descriptor")
    tiers = manifest.get("tier_policies", {})
    if set(tiers) != {"easy", "normal", "hard"}:
        raise PlayTraceError("diagnostic model tiers missing")
    for model in tiers.values():
        for key in ("checkpoint_sha256", "export_sha256"):
            if (
                not isinstance(model.get(key), str)
                or re.fullmatch(r"[a-f0-9]{64}", model[key]) is None
            ):
                raise PlayTraceError("invalid registered model identity")
        count, size = model.get("card_count"), model.get("observation_size")
        if (
            type(count) is not int
            or not 1 <= count <= 10000
            or type(size) is not int
            or size != 24 * count + 18 * 45 + 64
        ):
            raise PlayTraceError("invalid registered model dimensions")
        if model.get("observation_profile_id") not in {
            "full-faction-v1",
            "faction-unknown-v1",
        }:
            raise PlayTraceError("invalid registered model profile")
        temperature = model.get("temperature")
        if (
            type(temperature) not in (int, float)
            or not math.isfinite(temperature)
            or temperature < 0
        ):
            raise PlayTraceError("invalid registered model temperature")
    return {"build_identity": identity, "tiers": tiers}


def validate(events: list[dict], descriptor: dict, status: str) -> list[dict]:
    identity = descriptor["build_identity"]
    if (
        not events
        or events[0].get("schema") != SCHEMA
        or events[0].get("build_identity") != identity
        or events[0].get("event") != "diagnostic_start"
        or events[0].get("schema_version") != 1
    ):
        raise PlayTraceError("diagnostic header/build mismatch")
    records = []
    failures = []
    ended = False
    for index, event in enumerate(events):
        if event.get("seq") != index or ended:
            raise PlayTraceError("diagnostic sequence gap or event after end")
        kind = event.get("event")
        if index == 0:
            continue
        if kind == "diagnostic_end":
            if event.get("decisions") != len(records) or event.get("reason") != status:
                raise PlayTraceError("diagnostic completion count/status mismatch")
            ended = True
            continue
        if (
            kind not in {"policy_input", "policy_failure"}
            or set(event)
            != (POLICY_FIELDS if kind == "policy_input" else FAILURE_FIELDS)
            or event["decision_index"] != len(records) + len(failures)
        ):
            raise PlayTraceError("unknown/missing diagnostic fields or decision gap")
        observation, legal = event["observation"], event["legal_actions"]
        if (
            not isinstance(observation, dict)
            or set(observation) != OBS_FIELDS
            or event["seat"] != observation["seat"]
            or event["native_decision_id"] != observation["decision_id"]
        ):
            raise PlayTraceError("invalid actor observation contract")
        if (
            not isinstance(observation["players"], list)
            or len(observation["players"]) != 2
            or any(set(player) != PLAYER_FIELDS for player in observation["players"])
        ):
            raise PlayTraceError("non-public player fields in policy observation")
        difficulty = event["difficulty"]
        if type(difficulty) is not int or difficulty not in (1, 2, 3):
            raise PlayTraceError("invalid diagnostic difficulty")
        model = descriptor["tiers"][("easy", "normal", "hard")[difficulty - 1]]
        for field, expected in (
            ("checkpoint_sha256", model["checkpoint_sha256"]),
            ("weights_sha256", model["export_sha256"]),
            ("observation_profile_id", model["observation_profile_id"]),
            ("temperature", model["temperature"]),
        ):
            if event[field] != expected:
                raise PlayTraceError(
                    "diagnostic policy differs from registered build/model"
                )
        draws = event["random_draws"]
        if (
            not isinstance(draws, list)
            or len(draws) > 32
            or any(
                type(draw) not in (int, float)
                or not math.isfinite(draw)
                or not 0 <= draw < 1
                for draw in draws
            )
        ):
            raise PlayTraceError("invalid policy random-draw trace")
        if event["temperature"] == 0 and draws:
            raise PlayTraceError(
                "deterministic policy unexpectedly consumed randomness"
            )
        if kind == "policy_failure":
            category = event["error_category"]
            if (
                not isinstance(legal, list)
                or not isinstance(category, str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", category) is None
            ):
                raise PlayTraceError("invalid failed policy input/category")
            failures.append(event)
            continue
        chosen = event["selected_index"]
        if (
            not isinstance(legal, list)
            or not legal
            or type(chosen) is not int
            or not 0 <= chosen < len(legal)
            or event["selected_action"] != legal[chosen]
            or event["selected_action_id"] != legal[chosen].get("id")
        ):
            raise PlayTraceError("selected action disagrees with exact legal list")
        for key in ("encoded_observation", "group_logits", "local_logits"):
            if (
                not isinstance(event[key], list)
                or not event[key]
                or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in event[key]
                )
            ):
                raise PlayTraceError("invalid diagnostic numeric vector")
        if (
            len(event["encoded_observation"]) != model["observation_size"]
            or len(event["local_logits"]) != len(legal)
            or len(event["action_features"]) != len(legal)
            or len(event["action_group_ids"]) != len(legal)
            or len(event["group_features"]) != len(event["group_logits"])
        ):
            raise PlayTraceError("diagnostic observation/action/group shape mismatch")
        groups = len(event["group_logits"])
        ids = event["action_group_ids"]
        if any(
            type(group) is not int or not 0 <= group < groups for group in ids
        ) or set(ids) != set(range(groups)):
            raise PlayTraceError("diagnostic group ID out of range or missing")
        for rows, bounds in (
            (
                event["action_features"],
                (5, model["card_count"], model["card_count"], 3, model["card_count"]),
            ),
            (event["group_features"], (5, model["card_count"])),
        ):
            for row in rows:
                if (
                    not isinstance(row, list)
                    or len(row) != len(bounds)
                    or any(
                        type(value) is not int or not 0 <= value <= maximum
                        for value, maximum in zip(row, bounds, strict=True)
                    )
                ):
                    raise PlayTraceError("invalid diagnostic feature width/type/bounds")
        for feature, group in zip(event["action_features"], ids, strict=True):
            if feature[:2] != event["group_features"][group]:
                raise PlayTraceError("diagnostic group features disagree with action")
        value, outcomes, nonfinite = (
            event["value"],
            event["outcome_probabilities"],
            event["value_nonfinite"],
        )
        if type(nonfinite) is not bool:
            raise PlayTraceError("diagnostic value availability must be Boolean")
        if nonfinite:
            if value is not None or outcomes is not None:
                raise PlayTraceError(
                    "unavailable value must preserve explicit null outputs"
                )
        elif (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 1
            or not isinstance(outcomes, list)
            or len(outcomes) != 3
            or any(
                type(probability) not in (int, float)
                or not math.isfinite(probability)
                or not 0 <= probability <= 1
                for probability in outcomes
            )
            or abs(sum(outcomes) - 1) > 1e-6
            or abs(value - outcomes[1] * 0.5 - outcomes[2]) > 1e-6
        ):
            raise PlayTraceError("invalid diagnostic value/outcome outputs")
        records.append(event)
    if failures and status != "error":
        raise PlayTraceError(
            "failed network attempts cannot claim a complete diagnostic"
        )
    if status == "final" and not ended:
        raise PlayTraceError("missing diagnostic completion marker")
    return records


def collect(settings: Path, match: str, build_manifest: Path, output: Path) -> dict:
    manifest_bytes = build_manifest.read_bytes()
    descriptor = descriptor_from_manifest(json.loads(manifest_bytes))
    identity = descriptor["build_identity"]
    if (
        output.exists()
        or re.fullmatch(r"t[0-9]{9}", match) is None
        or re.fullmatch(r"[a-f0-9]{64}", identity) is None
    ):
        raise PlayTraceError("fresh output, exact match and build identity required")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    settings_bytes = settings.read_bytes()
    parser.read_string(settings_bytes.decode("utf-8"))
    if not parser.has_section(SECTION):
        raise PlayTraceError(
            "diagnostic section missing; public traces cannot substitute"
        )
    values = {}
    for key, raw in parser[SECTION].items():
        if not key.startswith(match + "_"):
            continue
        if raw.startswith('"'):
            if not raw.endswith('"') or len(raw) < 2:
                raise PlayTraceError("unterminated diagnostic native string")
            raw = raw[1:-1]
        values[key[len(match) + 1 :]] = raw
    status = values.get("status")
    if (
        status not in {"final", "interrupted", "error", "truncated"}
        or values.get("build") != identity
    ):
        raise PlayTraceError(
            "diagnostic is active, missing or belongs to another build"
        )
    if not values.get("count", "").isdecimal():
        raise PlayTraceError("invalid diagnostic chunk count")
    count = int(values["count"])
    chunks = {}
    for key, value in values.items():
        if key in {"build", "status", "count", "error"}:
            continue
        if re.fullmatch(r"[0-9]{9}", key) is None:
            raise PlayTraceError("diagnostic chunk key is not canonical")
        index = int(key)
        if index in chunks:
            raise PlayTraceError("duplicate diagnostic chunk index")
        chunks[index] = value
    if (
        count > 20000
        or set(chunks) != set(range(count))
        or any(not value or len(value) > 780 for value in chunks.values())
    ):
        raise PlayTraceError("diagnostic chunk gap, oversized chunk or truncation")
    events, jsonl = _native_events(
        "".join(chunks[index] for index in range(count)), match
    )
    records = validate(events, descriptor, status)
    failures = [event for event in events if event.get("event") == "policy_failure"]
    output.mkdir(parents=True)
    (output / "events.jsonl").write_text(jsonl, encoding="utf-8")
    (output / "policy-failures.json").write_text(
        json.dumps(
            {"schema": SCHEMA, "build_identity": identity, "failures": failures},
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    variants = [
        {
            "id": f"{match}-decision-{record['decision_index']:04d}",
            "observation": record["observation"],
            "legal_actions": record["legal_actions"],
            "expected": record,
        }
        for record in records
    ]
    (output / "policy-inputs.json").write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "build_identity": identity,
                "build_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "variants": variants,
                "complete": status == "final" and not failures,
                "failed_attempts": len(failures),
            },
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    summary = {
        "schema": SCHEMA,
        "build_identity": identity,
        "match": match,
        "status": status,
        "complete": status == "final",
        "decisions": len(records),
        "failed_attempts": len(failures),
        "chunks": count,
        "error": values.get("error", ""),
        "input_sha256": hashlib.sha256(settings_bytes).hexdigest(),
        "build_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "events_sha256": hashlib.sha256(jsonl.encode()).hexdigest(),
    }
    (output / "receipt.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--match", required=True)
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(collect(args.settings, args.match, args.build_manifest, args.output))
    )


if __name__ == "__main__":
    main()
