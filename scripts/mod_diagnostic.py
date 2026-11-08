"""Generate the opt-in policy-input diagnostic edition without changing release sources."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "mod/diagnostic/as3/GwaiPolicyDiagnostic.as"
SINK_PATH = ROOT / "mod/diagnostic/settings/scripts/local/gwai_policy_diagnostic.ws"
SCHEMA = "w3gwent-policy-diagnostic-v1"


class DiagnosticBuildError(ValueError):
    """A diagnostic-only hook or identity cannot be verified."""


def _once(source: str, anchor: str, replacement: str) -> str:
    if source.count(anchor) != 1:
        raise DiagnosticBuildError(f"expected one diagnostic hook: {anchor[:80]}")
    return source.replace(anchor, replacement, 1)


def prepare_sources(
    sources: dict[str, str], build: str, inputs: dict[str, str]
) -> tuple[dict[str, str], dict]:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", build):
        raise DiagnosticBuildError("invalid build identity")
    if "GwaiPolicyDiagnostic" in sources:
        raise DiagnosticBuildError("diagnostic helper already present")
    identity_inputs = {
        **inputs,
        **{
            name + ".as": hashlib.sha256(text.encode()).hexdigest()
            for name, text in sources.items()
        },
    }
    identity = hashlib.sha256(
        json.dumps(identity_inputs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = dict(sources)
    network = _once(
        result["GwaiNetPolicy"],
        "      _lastWinChance = NaN;\n",
        "      _lastWinChance = NaN;\n      try { GwaiPolicyDiagnostic.beginDecision(); } catch(gwaiDiagnosticBeginError:Error) {}\n",
    )
    network = _once(network, "Math.random()", "GwaiPolicyDiagnostic.random()")
    result["GwaiNetPolicy"] = _once(
        network,
        "      _lastWinChance = winChanceOf(result);\n",
        "      _lastWinChance = winChanceOf(result);\n      try { GwaiPolicyDiagnostic.record(observation,legalActions,result,difficulty,data,GwaiNetTierConfig.temperatureFor(difficulty)); } catch(gwaiDiagnosticError:Error) {}\n",
    )
    result["GwaiPolicyCore"] = _once(
        result["GwaiPolicyCore"],
        "            view.policyFallback = true;\n",
        "            view.policyFallback = true;\n            try { GwaiPolicyDiagnostic.failedAttempt(view.observation,legalMoves,difficulty,error.name,GwaiNetTierConfig.dataFor(difficulty),GwaiNetTierConfig.temperatureFor(difficulty)); } catch(gwaiDiagnosticFailureError:Error) {}\n",
    )
    trace = result["GwaiPlayTrace"]
    trace = _once(
        trace,
        "      if(!enabled) return;\n",
        "      GwaiPolicyDiagnostic.begin();\n      if(!enabled) return;\n",
    )
    anchor = "   public static function finish(reason:String, winner:int, manager:CardManager, flow:GwintGameFlowController, round:int) : void\n   {\n"
    trace = _once(
        trace, anchor, anchor + "      GwaiPolicyDiagnostic.finish(reason);\n"
    )
    anchor = "   private static function emit(record:Object) : void\n"
    trace = _once(
        trace,
        anchor,
        "   public static function encodeDiagnostic(record:Object) : String\n   {\n      return encodeValue(record,0);\n   }\n\n"
        + anchor,
    )
    result["GwaiPlayTrace"] = trace
    result["GwaiPolicyDiagnostic"] = (
        HELPER_PATH.read_text()
        .replace("__GWAI_DIAGNOSTIC_BUILD__", build)
        .replace("__GWAI_DIAGNOSTIC_ID__", identity)
    )
    return result, {
        "schema": SCHEMA,
        "build_identity": identity,
        "identity_inputs_sha256": identity_inputs,
        "automatic": True,
        "settings_section": "W3GwentPolicyDiagnostic",
        "information": "exact acting-seat policy input, including own hand; no opponent hand identities or deck order",
        "maximum_decisions": 512,
        "maximum_chunks": 20000,
        "chunk_characters": 780,
        "separate_from_public_trace": True,
        "second_forward_pass": False,
    }


def verify_compiled(helpers: dict[str, str]) -> None:
    expected = {
        "GwaiNetPolicy": (
            "GwaiPolicyDiagnostic.record(",
            "GwaiPolicyDiagnostic.beginDecision()",
            "GwaiPolicyDiagnostic.random()",
        ),
        "GwaiPolicyCore": ("GwaiPolicyDiagnostic.failedAttempt(",),
        "GwaiPlayTrace": (
            "GwaiPolicyDiagnostic.begin()",
            "GwaiPolicyDiagnostic.finish(",
            "functionencodeDiagnostic(",
        ),
        "GwaiPolicyDiagnostic": (
            "OnW3GwentPolicyDiagnosticRecord",
            "encoded_observation",
            "observation_profile_id",
            "checkpoint_sha256",
            "group_logits",
            "selected_action_id",
            "random_draws",
            "Math.random()",
        ),
    }
    for name, markers in expected.items():
        compact = "".join(helpers[name].split())
        for marker in markers:
            if marker not in compact:
                raise DiagnosticBuildError(
                    f"compiled diagnostic helper missing {name}:{marker}"
                )
