"""Build one shared model with controller difficulty from local developer inputs."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .bundle import (
    CONTROLLER_CONTRACT,
    COUNTER_CONTRACT,
    INFORMATION_PROTOCOL,
    SELECTION_RULE,
)
from .compiler import build_player
from .inputs import ROOT, descriptor, prepare


def shared_observation_profile(checkpoint_sha256, *, declared=None, requested=None):
    """Preserve audited legacy training inputs without relabelling bundles."""
    from scripts.policy_format import (
        FACTION_UNKNOWN_CHECKPOINTS,
        UNKNOWN_FACTION_PROFILE,
        validate_observation_profile,
    )

    if checkpoint_sha256 in FACTION_UNKNOWN_CHECKPOINTS:
        if declared is not None and declared != UNKNOWN_FACTION_PROFILE:
            raise ValueError(
                "audited shared model requires faction-unknown-v1; rebuild the inference bundle and its independent probes from the checkpoint"
            )
        if requested is not None and requested != UNKNOWN_FACTION_PROFILE:
            raise ValueError(
                "audited shared model observation profile cannot be changed"
            )
        return UNKNOWN_FACTION_PROFILE
    if requested is not None:
        validate_observation_profile(requested)
        if declared is not None and requested != declared:
            raise ValueError(
                "inference bundle profiles are immutable; regenerate its independent probes"
            )
        return requested
    return declared


def build(
    *,
    checkpoint=None,
    policy_bundle=None,
    catalog=None,
    output=None,
    observation_profile=None,
    diagnostic=False,
):
    catalog = Path(catalog or ROOT / ".runtime/catalog.json")
    output = Path(
        output
        or ROOT
        / (".runtime/mod-build-diagnostic" if diagnostic else ".runtime/mod-build")
    )
    if output.exists():
        raise FileExistsError("fresh shared build output required")
    work = output.parent / (output.name + "-inputs")
    if work.exists():
        raise FileExistsError("fresh shared build input directory required")
    exported = work / "model/export"
    exported.mkdir(parents=True)
    if policy_bundle is not None:
        from scripts.policy_bundle import load_bundle

        bundle = load_bundle(Path(policy_bundle))
        metadata = dict(bundle.metadata["hard"])
        shared_observation_profile(
            metadata["checkpoint_sha256"],
            declared=metadata["observation_profile_id"],
            requested=observation_profile,
        )
        shutil.copyfile(bundle.weights_paths["hard"], exported / "policy.f32.bin")
        if bundle.probes_path is None:
            raise ValueError("inference bundle must include independent numeric probes")
        probes = bundle.probes_path
        from scripts.policy_probes import validate_model_probes

        validate_model_probes(probes, metadata)
    else:
        from scripts.export_policy_as3 import export
        from scripts.policy_probes import create_probes

        checkpoint = Path(checkpoint or ROOT / ".runtime/models/hard.pt")
        profile = shared_observation_profile(
            hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            requested=observation_profile,
        )
        metadata = export(checkpoint, catalog, exported, observation_profile_id=profile)
        probes = work / "numeric-probes.json"
        create_probes(
            {"hard": checkpoint},
            catalog,
            probes,
            observation_profile_id=metadata["observation_profile_id"],
        )
    metadata.update(
        data_kind="inference-only",
        controller_contract=CONTROLLER_CONTRACT,
        counter_contract=COUNTER_CONTRACT,
        selection_rule=SELECTION_RULE,
        information_protocol=INFORMATION_PROTOCOL,
        controller_source_sha256=descriptor(
            ROOT / "mod/ai/as3/GwaiSharedController.as"
        )["sha256"],
        training_manifest_sha256="unrecorded",
    )
    (exported / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    receipt = work / "model/conversion.json"
    receipt.write_text(
        json.dumps(
            {
                "format": "shared-inference-export-v1",
                "checkpoint_sha256": metadata["checkpoint_sha256"],
                "payload_sha256": metadata["weights_sha256"],
                "training_provenance": "unrecorded",
                "strength_evidence": False,
            },
            indent=2,
        )
        + "\n"
    )
    controller = work / "controller.json"
    controller.write_text(
        json.dumps(
            {
                "enabled": True,
                "mode": "policy-only",
                "terminal_pass_v1": True,
                "difficulties": {
                    str(i): {
                        "temperature": t,
                        "planning_worlds": 0,
                        "rollout_primary_turns": 0,
                        "deadline_ms": 0,
                    }
                    for i, t in enumerate((1.0, 0.25, 0.0), 1)
                },
            },
            indent=2,
        )
        + "\n"
    )
    manifest = prepare(
        metadata=exported / "metadata.json",
        receipt=receipt,
        catalog=catalog,
        controller=controller,
        fictional_probes=probes,
        actual_probes=None,
        output=work / "compiler",
        edition="diagnostic" if diagnostic else "release",
    )
    data = json.loads(manifest.read_text())
    data.update(
        purpose="shared-player-inference-compile", artifact_kind="inference-only"
    )
    manifest.write_text(json.dumps(data, indent=2) + "\n")
    return build_player(manifest, output)
