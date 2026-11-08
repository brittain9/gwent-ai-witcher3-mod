"""Generate one-model AS3 helper inventories without invoking a compiler."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .bundle import (
    METADATA_FIELDS,
    PreparedSharedPolicy,
    SharedBundleError,
    _header,
    _json,
    _payload,
    validate_controller,
    validate_metadata,
)

AS3_ROOT = Path(__file__).resolve().parents[1] / "mod" / "ai" / "as3"
MARKER = "/*__SHARED_DIAGNOSTIC_RECORD__*/"


def shared_numeric_source():
    """Exclude the separately selectable historical tier facade from shared builds."""
    from .host import _body

    text = (AS3_ROOT / "GwaiNetPolicy.as").read_text(encoding="utf-8")
    for name in (
        "decide",
        "lastWinChance",
        "insight",
        "winChanceOf",
        "provenance",
        "legacyActionUniforms",
        "legacyUniform",
    ):
        start, end = _body(text, name)
        declaration = max(
            text.rfind("   public static function ", 0, start),
            text.rfind("   private static function ", 0, start),
        )
        text = text[:declaration] + text[end + 1 :]
    return text.replace("   private static var _lastWinChance:Number = NaN;", "")


def build_sources(
    prepared: PreparedSharedPolicy,
    *,
    edition: str = "release",
    diagnostic_mode: str = "policy",
) -> dict[str, str]:
    """Return an exact class inventory; release has no capture class or call."""
    if edition not in {"release", "diagnostic"}:
        raise SharedBundleError("edition must be release or diagnostic")
    if diagnostic_mode not in {"policy", "timing-only"} or (
        edition != "diagnostic" and diagnostic_mode != "policy"
    ):
        raise SharedBundleError("timing-only mode requires private diagnostic edition")
    metadata = validate_metadata(
        {name: prepared.metadata[name] for name in METADATA_FIELDS}
    )
    class_name = f"GwaiNetWeightsData_{metadata['checkpoint_sha256'][:16]}"
    if prepared.class_name != class_name:
        raise SharedBundleError("prepared shared model class identity differs")
    if _json(prepared.metadata_path) != prepared.metadata:
        raise SharedBundleError("prepared shared model metadata changed")
    payload = _payload(prepared.weights_path, metadata)
    header = _header(payload, prepared.metadata, class_name)
    if prepared.header_path.read_text(encoding="utf-8") != header:
        raise SharedBundleError("prepared shared model header changed")
    controller = validate_controller(_json(prepared.controller_path))
    template = (AS3_ROOT / "GwaiSharedController.as").read_text(encoding="utf-8")
    if template.count(MARKER) != 1:
        raise SharedBundleError(
            "shared controller diagnostic seam is missing or ambiguous"
        )
    record = (
        "GwaiSharedDiagnostic.record(this._observation,this._legal,this.output,this.value,result,this._difficulty);"
        if edition == "diagnostic"
        else ""
    )
    if edition == "diagnostic" and diagnostic_mode == "timing-only":
        before = "      var result:Object = GwaiNetPolicy.evaluate("
        after = "         Number(this._settings.temperature),String(data.OBSERVATION_PROFILE_ID),this._actionUniforms);"
        if template.count(before) != 1 or template.count(after) != 1:
            raise SharedBundleError("private model timing seam differs")
        weights = (
            "         GwaiNetWeights.load(data),data.OFFSETS,data.WIDTH,data.BLOCKS,"
        )
        if template.count(weights) != 1:
            raise SharedBundleError("private weights timing seam differs")
        template = template.replace(
            weights,
            "         gwaiTimingWeights,data.OFFSETS,data.WIDTH,data.BLOCKS,",
            1,
        )
        template = template.replace(
            before,
            '      GwaiSharedDiagnostic.timingMark(int(this._observation.seat),"weights_begin",getTimer());\n'
            "      var gwaiTimingWeights:Vector.<Number> = GwaiNetWeights.load(data);\n"
            '      GwaiSharedDiagnostic.timingMark(int(this._observation.seat),"weights_returned",getTimer());\n'
            '      GwaiSharedDiagnostic.timingMark(int(this._observation.seat),"model_begin",getTimer());\n'
            + before,
            1,
        )
        template = template.replace(
            after,
            after
            + '\n      GwaiSharedDiagnostic.timingMark(int(this._observation.seat),"model_returned",getTimer());',
            1,
        )
    sources = {
        "GwaiNetPolicy": shared_numeric_source(),
        "GwaiNetWeights": (AS3_ROOT / "GwaiNetWeights.as").read_text(encoding="utf-8"),
        "GwaiSharedController": template.replace(MARKER, record),
        "GwaiSharedConfig": _configuration(controller, metadata, class_name),
        class_name: header,
    }
    if edition == "diagnostic":
        sources["GwaiSharedDiagnostic"] = (
            AS3_ROOT / "GwaiSharedDiagnostic.as"
        ).read_text(encoding="utf-8")
    if "TierConfig" in "\n".join(sources.values()):
        raise SharedBundleError("shared player contains tier-specific model selection")
    return sources


def _configuration(
    controller: dict[str, Any], metadata: dict[str, Any], class_name: str
) -> str:
    lines = [
        "internal class GwaiSharedConfig",
        "{",
        f"   public static function enabled() : Boolean {{ return {str(controller['enabled']).lower()}; }}",
        "   public static function terminalPassEnabled() : Boolean { return "
        + str(controller.get("terminal_pass_v1", False)).lower()
        + "; }",
        '   public static function mode() : String { return "policy-only"; }',
        f"   public static function data() : Object {{ return {class_name}; }}",
        "   public static function settingsFor(difficulty:int) : Object",
        "   {",
    ]
    for level, settings in sorted(controller["difficulties"].items()):
        if "terminal_pass_v1" in controller:
            settings = {**settings, "terminal_pass_v1": controller["terminal_pass_v1"]}
        literal = json.dumps(settings, separators=(",", ":"), allow_nan=False)
        lines.append(f"      if(difficulty == {level}) return {literal};")
    lines.extend(['      throw new Error("difficulty outside 1..3");', "   }"])
    for field in (
        "data_kind",
        "controller_contract",
        "counter_contract",
        "selection_rule",
        "information_protocol",
        "observation_profile_id",
        "controller_source_sha256",
        "training_manifest_sha256",
    ):
        value = json.dumps(metadata[field], allow_nan=False)
        lines.append(f"   public static const {field.upper()}:String = {value};")
    lines.extend(["}", ""])
    return "\n".join(lines)


def source_identity(sources: dict[str, str]) -> dict[str, Any]:
    """Hash each generated helper, including edition-specific source content."""
    model_count = sum(name.startswith("GwaiNetWeightsData_") for name in sources)
    if model_count != 1:
        raise SharedBundleError("source inventory must contain exactly one model")
    files = {
        name: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for name, text in sorted(sources.items())
    }
    digest = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema": 1,
        "sha256": digest,
        "classes": files,
        "model_count": model_count,
        "mode": "policy-only",
        "native_verified": False,
        "numerical_verified": False,
        "strength_evidence": False,
    }
