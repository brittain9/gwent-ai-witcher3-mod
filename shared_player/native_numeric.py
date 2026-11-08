"""Truthful pure native forward checks, with critic and policy fields separate."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import struct
import time
from pathlib import Path

from .bundle import _json, _sha, _write
from .legacy import verify_baseline_binding

TOLERANCE = 1e-4


class NativeNumericError(ValueError):
    """A native numeric input or forward result differs from its reference."""


def _need(condition, message):
    if not condition:
        raise NativeNumericError(message)


def _error(actual, expected):
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return math.inf
        return max(
            [0.0, *(_error(a, b) for a, b in zip(actual, expected, strict=True))]
        )
    if (
        type(actual) not in (int, float)
        or type(expected) not in (int, float)
        or not math.isfinite(actual)
        or not math.isfinite(expected)
    ):
        return math.inf
    return abs(actual - expected)


def _float_hash(values):
    if not isinstance(values, list) or any(
        type(value) not in (int, float) or not math.isfinite(value) for value in values
    ):
        return None
    try:
        return hashlib.sha256(
            b"".join(struct.pack("<f", value) for value in values)
        ).hexdigest()
    except (OverflowError, struct.error):
        return None


def compare_native(native, expected):
    """Compare encoder, logits, explicit critic WDL and exact served index."""
    logits_error = max(
        _error(native.get("group_logits"), expected["group_logits"]),
        _error(native.get("local_logits"), expected["local_logits"]),
    )
    value_error = _error(native.get("value"), expected["value"])
    critic_error = _error(
        native.get("critic_probabilities"), expected["outcome_probabilities"]
    )
    critic = native.get("critic_probabilities")
    critic_valid = (
        native.get("critic_class_order") == ["loss", "draw", "win"]
        and isinstance(critic, list)
        and len(critic) == 3
        and all(
            type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1
            for value in critic
        )
        and abs(math.fsum(critic) - 1) <= TOLERANCE
    )
    encoder_pass = (
        _float_hash(native.get("observation_features"))
        == expected["observation_sha256"]
        and native.get("action_features") == expected["action_features"]
        and native.get("group_features") == expected["group_features"]
        and native.get("action_groups") == expected["action_group_ids"]
    )
    index = native.get("served_action_index")
    index_disagrees = type(index) is not int or index != expected["chosen_index"]
    identical = (
        index_disagrees
        and type(index) is int
        and 0 <= index < len(expected["action_features"])
        and expected["action_features"][index]
        == expected["action_features"][expected["chosen_index"]]
    )
    invalid = not critic_valid or not all(
        math.isfinite(value) for value in (logits_error, value_error, critic_error)
    )
    return {
        "pass": not invalid
        and encoder_pass
        and not index_disagrees
        and max(logits_error, value_error, critic_error) <= TOLERANCE,
        "encoder_pass": encoder_pass,
        "served_index_disagreement": index_disagrees,
        "identical_feature_disagreement": identical,
        "invalid": invalid,
        "logits_max_abs_error": logits_error if math.isfinite(logits_error) else None,
        "value_max_abs_error": value_error if math.isfinite(value_error) else None,
        "critic_wdl_max_abs_error": critic_error
        if math.isfinite(critic_error)
        else None,
        "critic_class_order": ["loss", "draw", "win"],
        "policy_prior_compared": False,
        "tolerance": TOLERANCE,
    }


def numeric_fixture_metadata(
    metadata, *, card_ids, card_effects, source_metadata_sha256
):
    """A synthetic numeric descriptor cannot enter ordinary player admission."""
    _need(
        metadata.get("data_kind") == "gameplay",
        "original model provenance must remain explicit gameplay",
    )
    _sha(source_metadata_sha256, "original numeric metadata")
    _need(
        card_ids == list(range(1, metadata["card_count"] + 1)),
        "fictional numeric vocabulary must be canonical ordinals",
    )
    _need(
        isinstance(card_effects, list)
        and len(card_effects) == len(card_ids)
        and all(
            isinstance(effects, list)
            and all(type(effect) is int and 0 <= effect < 45 for effect in effects)
            for effects in card_effects
        ),
        "fictional numeric effects differ",
    )
    return {
        **copy.deepcopy(metadata),
        "data_kind": "synthetic",
        "original_model_data_kind": metadata["data_kind"],
        "numeric_fixture_only": True,
        "numeric_fixture_scope": "synthetic-schema-forward-only-never-player-or-training",
        "original_metadata_sha256": source_metadata_sha256,
        "card_ids": copy.deepcopy(card_ids),
        "card_effects": copy.deepcopy(card_effects),
    }


def _spec(spec):
    path = Path(spec["path"])
    _need(
        path.is_absolute() and path.is_file() and not path.is_symlink(),
        "explicit regular native numeric input required",
    )
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    _need(digest == spec["sha256"], f"native numeric input changed: {path.name}")
    return path


def _transport(observation):
    view, changes = copy.deepcopy(observation), []
    _need(
        type(view.get("seat")) is int and view["seat"] in (0, 1),
        "numeric view needs an acting seat",
    )
    _need(
        not {"snapshot", "full_state", "opponent_hand", "opponent_deck"}.intersection(
            view
        ),
        "privileged numeric input forbidden",
    )
    if "terminal" not in view or view["terminal"] is False:
        view["terminal"] = None
        changes.append("terminal:false-or-absent-to-null")
    _need(
        view["terminal"] is None or isinstance(view["terminal"], dict),
        "unsupported numeric terminal transport",
    )
    if "to_move" not in view:
        view["to_move"] = view["seat"]
        changes.append("omitted-to_move-to-seat")
    _need(
        type(view["to_move"]) is int and view["to_move"] in (0, 1),
        "unsupported numeric acting-seat transport",
    )
    return view, changes


def primary_case_plan(data, checkpoint_sha256):
    """Classify unchanged authored probes against the deployed primary boundary."""
    fictional = data["format"] == "witcher3-policy-probes-v1"
    model = data["models"][checkpoint_sha256]
    source_count = 200 if fictional else model["header"]["position_count"]
    records = model["cases"]
    _need(
        type(source_count) is int and source_count > 0 and len(records) == source_count,
        "native numeric source case count differs",
    )
    selected, omitted = [], []
    for index, record in enumerate(records):
        if record["observation"].get("pending_choice") is not None:
            _need(fictional, "actual-primary dataset contains a pending ability")
            omitted.append(
                {
                    "source_index": index,
                    "position_id": record["position_id"],
                    "reason": "native-pending-choice-not-primary",
                    "pending_kind": record["observation"]["pending_choice"].get("kind"),
                }
            )
        else:
            selected.append({"source_index": index, "record": record})
    _need(bool(selected), "native numeric dataset has no primary probes")
    return {
        "source_count": source_count,
        "selected": selected,
        "omitted": omitted,
        "selection_rule": "unchanged-primary-only-v1",
    }


def run_native_gate(manifest_path, output_dir):
    """Use only the pure probe endpoint; retain every bounded batch and result."""
    from w3gwent.engine import EngineClient

    manifest = _json(Path(manifest_path))
    _need(
        manifest.get("schema") == 1
        and manifest.get("purpose") == "shared-player-native-numeric",
        "unsupported native numeric manifest",
    )
    output_dir = Path(output_dir)
    _need(
        not output_dir.exists() and not output_dir.is_symlink(),
        "native numeric output must be fresh",
    )
    engine, metadata_path, weights_path, receipt_path = (
        _spec(manifest[name])
        for name in ("engine", "metadata", "weights", "baseline_receipt")
    )
    metadata = _json(metadata_path)
    provenance = verify_baseline_binding(metadata, receipt_path)
    _need(
        manifest["weights"]["sha256"]
        == metadata["weights_sha256"]
        == provenance["payload_sha256"],
        "native numeric model payload differs",
    )
    _need(
        type(manifest.get("batch_size")) is int and 1 <= manifest["batch_size"] <= 8,
        "numeric batch size must respect the registered response cap",
    )
    timeout = manifest.get("engine_timeout_seconds")
    _need(
        type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0,
        "native numeric request timeout must be explicit",
    )
    datasets = [
        (_spec(spec), _json(Path(spec["path"]), limit=16 * 1024 * 1024))
        for spec in manifest["datasets"]
    ]
    _need(
        {data["format"] for _, data in datasets}
        == {"witcher3-policy-probes-v1", "witcher3-shared-actual-primary-probes-v1"},
        "both numeric datasets required",
    )
    plans = []
    for spec, (path, data) in zip(manifest["datasets"], datasets, strict=True):
        plan = primary_case_plan(data, metadata["checkpoint_sha256"])
        _need(
            spec.get("selection_rule") == plan["selection_rule"]
            and spec.get("expected_source_positions") == plan["source_count"]
            and spec.get("expected_selected_positions") == len(plan["selected"])
            and spec.get("expected_omitted_positions") == len(plan["omitted"]),
            "registered primary subset counts/rule differ",
        )
        plans.append(plan)
    planned_count = sum(len(plan["selected"]) for plan in plans)
    _need(
        planned_count == manifest["expected_positions"],
        "registered total primary numeric position count differs",
    )
    output_dir.mkdir(parents=True)
    _write(output_dir / "registered-manifest.json", manifest)
    _write(
        output_dir / "primary-selection.json",
        [
            {
                "dataset_sha256": spec["sha256"],
                "selection_rule": plan["selection_rule"],
                "source_positions": plan["source_count"],
                "selected_source_indices": [
                    item["source_index"] for item in plan["selected"]
                ],
                "selected_position_ids": [
                    item["record"]["position_id"] for item in plan["selected"]
                ],
                "omissions": plan["omitted"],
            }
            for spec, plan in zip(manifest["datasets"], plans, strict=True)
        ],
    )
    started, stage, completed, expected_total, batch_index = (
        time.perf_counter(),
        "worker_start",
        [],
        planned_count,
        0,
    )
    try:
        # Each descriptor has its own immutable loaded-policy identity.
        # Close its owned worker before opening the next; never permit reload.
        for (dataset_path, data), plan in zip(datasets, plans, strict=True):
            with EngineClient([engine], timeout=timeout) as client:
                model = data["models"][metadata["checkpoint_sha256"]]
                fictional = data["format"] == "witcher3-policy-probes-v1"
                count = len(plan["selected"])
                _need(
                    model["header"]["observation_profile_id"]
                    == metadata["observation_profile_id"]
                    and model["header"]["catalog_sha256"] == metadata["catalog_sha256"],
                    "native numeric reference identity differs",
                )
                current_metadata = metadata_path
                if fictional:
                    fixture = numeric_fixture_metadata(
                        metadata,
                        card_ids=model["card_ids"],
                        card_effects=model["card_effects"],
                        source_metadata_sha256=manifest["metadata"]["sha256"],
                    )
                    current_metadata = output_dir / "synthetic-fixture-metadata.json"
                    _write(current_metadata, fixture)
                    _write(
                        output_dir / "synthetic-fixture-scope.json",
                        {
                            "original_metadata_sha256": manifest["metadata"]["sha256"],
                            "derived_metadata_sha256": hashlib.sha256(
                                current_metadata.read_bytes()
                            ).hexdigest(),
                            "checkpoint_unchanged": True,
                            "weights_unchanged": True,
                            "ordinary_player_admissible": False,
                            "ordinary_conversion_binding_admissible": False,
                        },
                    )
                for offset in range(0, count, manifest["batch_size"]):
                    stage = f"batch-{batch_index}"
                    selected = plan["selected"][
                        offset : offset + manifest["batch_size"]
                    ]
                    records = [item["record"] for item in selected]
                    probes, transports = [], []
                    for record in records:
                        view, changes = _transport(record["observation"])
                        probes.append(
                            {
                                "observation": view,
                                "legal_actions": record["legal_actions"],
                            }
                        )
                        transports.append(changes)
                    request = {
                        "policy_metadata": str(current_metadata),
                        "policy_weights": str(weights_path),
                        "observation_profile_id": metadata["observation_profile_id"],
                        "probes": probes,
                    }
                    _write(
                        output_dir / f"batch-{batch_index:03d}-request.json",
                        {
                            "dataset_sha256": hashlib.sha256(
                                dataset_path.read_bytes()
                            ).hexdigest(),
                            "selected_offset": offset,
                            "source_indices": [
                                item["source_index"] for item in selected
                            ],
                            "transport_changes": transports,
                            "request": request,
                        },
                    )
                    response = client.request("native_network_probe", **request)
                    _write(
                        output_dir / f"batch-{batch_index:03d}-response.json", response
                    )
                    inferences = response.get("inferences")
                    _need(
                        isinstance(inferences, list)
                        and len(inferences) == len(records),
                        "native numeric batch lost or added positions",
                    )
                    for record, inference in zip(records, inferences, strict=True):
                        _need(
                            isinstance(inference, dict)
                            and "critic_probabilities" in inference
                            and inference.get("critic_class_order")
                            == ["loss", "draw", "win"],
                            "native endpoint lacks the explicit critic WDL contract; policy probabilities cannot substitute",
                        )
                        indices = [
                            index
                            for index, action in enumerate(record["legal_actions"])
                            if action["id"] == record["expected"]["action_id"]
                        ]
                        _need(
                            len(indices) == 1, "reference action is absent or ambiguous"
                        )
                        comparison = compare_native(
                            inference,
                            {**record["expected"], "chosen_index": indices[0]},
                        )
                        completed.append(
                            {
                                "dataset_format": data["format"],
                                "position_id": record["position_id"],
                                "batch": batch_index,
                                **comparison,
                            }
                        )
                    batch_index += 1
        for name in ("engine", "metadata", "weights", "baseline_receipt"):
            _spec(manifest[name])
        for spec in manifest["datasets"]:
            _spec(spec)

        def maximum(field):
            values = [row[field] for row in completed]
            return (
                max(values)
                if values and all(value is not None for value in values)
                else None
            )

        report = {
            "status": "complete",
            "pass": len(completed) == expected_total == manifest["expected_positions"]
            and all(row["pass"] for row in completed),
            "positions": len(completed),
            "source_positions": sum(plan["source_count"] for plan in plans),
            "omitted_native_pending_positions": sum(
                len(plan["omitted"]) for plan in plans
            ),
            "selection_rule": "unchanged-primary-only-v1",
            "expected_positions": expected_total,
            "batches": batch_index,
            "logits_max_abs_error": maximum("logits_max_abs_error"),
            "value_max_abs_error": maximum("value_max_abs_error"),
            "critic_wdl_max_abs_error": maximum("critic_wdl_max_abs_error"),
            "critic_class_order": ["loss", "draw", "win"],
            "served_index_disagreements": sum(
                row["served_index_disagreement"] for row in completed
            ),
            "identical_feature_disagreements": sum(
                row["identical_feature_disagreement"] for row in completed
            ),
            "encoder_mismatches": sum(not row["encoder_pass"] for row in completed),
            "invalid_positions": sum(row["invalid"] for row in completed),
            "tolerance": TOLERANCE,
            "policy_prior_compared": False,
            "wall_seconds": time.perf_counter() - started,
            "player_latency_evidence": False,
            "native_gameplay_evidence": False,
            "strength_evidence": False,
            "results": completed,
        }
        _write(output_dir / "result.json", report)
        _need(
            report["pass"],
            "native numerical gate has mismatches; inspect retained batches/results",
        )
        return report
    except Exception as error:
        _write(
            output_dir / "failure.json",
            {
                "stage": stage,
                "type": type(error).__name__,
                "message": str(error),
                "positions_compared": len(completed),
                "positions_uncompared": expected_total - len(completed),
                "expected_positions": expected_total,
                "batches_fully_compared": batch_index,
                "wall_seconds": time.perf_counter() - started,
                "results": completed,
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_native_gate(args.manifest, args.output)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "pass",
                    "positions",
                    "batches",
                    "logits_max_abs_error",
                    "value_max_abs_error",
                    "critic_wdl_max_abs_error",
                    "served_index_disagreements",
                )
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
