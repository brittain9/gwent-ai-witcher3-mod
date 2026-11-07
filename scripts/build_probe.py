#!/usr/bin/env python3
"""Build an offline, private Gwent decision-injection probe with local FFDec."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VANILLA = ROOT / ".local/vanilla/gameplay/gui_new/swf/gwint"
VANILLA_MANIFEST = ROOT / ".local/vanilla/manifest.json"
AS3_MANIFEST = ROOT / ".local/as3/manifest.json"
FFDEC_JAR = ROOT / ".local/tools/ffdec/ffdec-cli.jar"
AS3_DIR = ROOT / "mod/probe/as3"
RECORDER_SOURCE = ROOT / "mod/probe/recorder/gwai_probe_trace.ws"
OUTPUT_DIR = ROOT / ".runtime/probe"

PACKAGE = "red.game.witcher3.menus.gwint"
PACKAGE_PATH = Path("red/game/witcher3/menus/gwint")
SWF_RELATIVE_PATH = "gameplay/gui_new/swf/gwint/gwint_game.swf"
REDSWF_RELATIVE_PATH = "gameplay/gui_new/swf/gwint/gwint_game.redswf"
PROBE_SCHEMA = "gwai-probe-trace-v1"
SWF_MAX_SIZE = 256 * 1024 * 1024
CFX_TRAILER_MAX_SIZE = 4 * 1024 * 1024

# Pins match the extracted Steam build recorded in .local/vanilla/manifest.json.
# A changed game resource or FFDec jar requires a deliberate re-review.
# Manifest hashes are recorded per build: extraction manifests include local
# installation paths. Their source and per-file attestations are checked below.
PINNED_INPUT_SHA256 = {
    "gwint_game.redswf": "aeb1255be5a4d218bf41a7630dd27e4b25b32936c0f06055fa77e7aec4d5664e",
    "gwint_game.swf": "3edfd732b187261bc330a02b84577f95c1dce11c295a659590db898d2403e7dc",
    "ffdec-cli.jar": "84573b0e260f2288ddb12afd54cdba853e1df77ca2514d6cff0c3a875f1e70cd",
}

AI_DECISION_ANCHOR = (
    "         CardManager.getInstance().CalculateCardPowerPotentials();"
)
AI_IMPORT_ANCHOR = "   import flash.events.TimerEvent;"
BASE_APPLY_ANCHOR = (
    "      protected function state_begin_ApplyingCard() : void\n      {\n"
)
AI_INJECTION = """\n         var gwaiProbeTransaction:CardTransaction = GwaiProbeChoice.firstSupportedTransaction(this);
         if(gwaiProbeTransaction != null)
         {
            _decidedCardTransaction = gwaiProbeTransaction;
            this.attitude = TACTIC_MAXIMIZE_WIN;
            GwaiProbeTrace.selected(this.playerID,gwaiProbeTransaction);
            return;
         }
         GwaiProbeTrace.reject(\"no_supported_injected_action\");"""
BASE_INJECTION = "         if(this is AIPlayerController)\n         {\n            GwaiProbeTrace.applyStarted(this.playerID,this._decidedCardTransaction);\n         }\n"


class ProbeBuildError(RuntimeError):
    """Raised when an input, hook, or offline compilation check is ambiguous."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require_exactly_once(
    source: str,
    anchor: str,
    replacement: str,
    label: str,
    *,
    before: bool = False,
) -> str:
    count = source.count(anchor)
    if count != 1:
        raise ProbeBuildError(f"{label}: expected one exact hook anchor, found {count}")
    value = replacement + anchor if before else anchor + replacement
    return source.replace(anchor, value, 1)


def normalize_swf(data: bytes, label: str) -> bytes:
    if len(data) < 8:
        raise ProbeBuildError(f"{label}: truncated SWF header")
    signature = data[:3]
    declared_length = struct.unpack_from("<I", data, 4)[0]
    if declared_length != len(data):
        if signature == b"CWS":
            inflater = zlib.decompressobj()
            try:
                body = inflater.decompress(data[8:], declared_length - 8 + 1)
            except zlib.error as exc:
                raise ProbeBuildError(
                    f"{label}: invalid compressed SWF: {exc}"
                ) from exc
            if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
                raise ProbeBuildError(
                    f"{label}: incomplete or trailing compressed SWF data"
                )
            if len(body) != declared_length - 8:
                raise ProbeBuildError(f"{label}: SWF length does not match its header")
            data = b"FWS" + data[3:8] + body
        else:
            raise ProbeBuildError(f"{label}: unsupported signature or SWF length")
    elif signature == b"CWS":
        inflater = zlib.decompressobj()
        try:
            body = inflater.decompress(data[8:], declared_length - 8 + 1)
        except zlib.error as exc:
            raise ProbeBuildError(f"{label}: invalid compressed SWF: {exc}") from exc
        if (
            not inflater.eof
            or inflater.unused_data
            or inflater.unconsumed_tail
            or len(body) != declared_length - 8
        ):
            raise ProbeBuildError(f"{label}: compressed SWF length is inconsistent")
        data = b"FWS" + data[3:8] + body
    elif signature != b"FWS":
        raise ProbeBuildError(f"{label}: expected FWS or CWS, found {signature!r}")
    if data[:3] != b"FWS" or struct.unpack_from("<I", data, 4)[0] != len(data):
        raise ProbeBuildError(f"{label}: normalized SWF header is invalid")
    if not data.endswith(b"\x00\x00"):
        raise ProbeBuildError(f"{label}: SWF does not end with an End tag")
    return data


def _read_chunk_table(redswf: bytes) -> tuple[list[list[int]], int, int]:
    if len(redswf) < 0x28 + 10 * 12:
        raise ProbeBuildError("redswf: truncated CR2W chunk table header")
    table = [
        list(struct.unpack_from("<III", redswf, 0x28 + index * 12))
        for index in range(10)
    ]
    chunk_offset, chunk_count, _ = table[4]
    if (
        chunk_count < 1
        or chunk_count > 65535
        or chunk_offset < 0x28 + 10 * 12
        or chunk_offset + chunk_count * 24 > len(redswf)
    ):
        raise ProbeBuildError("redswf: invalid first chunk table bounds")
    return table, chunk_offset, chunk_count


def unpack_redswf(redswf: bytes) -> tuple[bytes, bytes]:
    _, chunk_offset, chunk_count = _read_chunk_table(redswf)
    chunks = [
        list(struct.unpack_from("<HHIIIII", redswf, chunk_offset + index * 24))
        for index in range(chunk_count)
    ]
    first_size = chunks[0][3]
    first_offset = chunks[0][4]
    if first_offset < 4 or first_size < 12 or first_offset + first_size > len(redswf):
        raise ProbeBuildError("redswf: invalid first chunk bounds")
    _, _, _, swf, trailer = _locate_cfx_payload(redswf, first_offset, first_size)
    return swf, trailer


def _decode_cfx_candidate(
    redswf: bytes, tag_offset: int, chunk_end: int
) -> tuple[int, bytes, bytes] | None:
    """Validate one length-delimited CFX candidate and return its payload."""
    if tag_offset < 4:
        return None
    cfx_length = struct.unpack_from("<I", redswf, tag_offset - 4)[0]
    cfx_end = tag_offset + cfx_length
    if cfx_length < 8 or cfx_end > chunk_end:
        return None
    version = redswf[tag_offset + 3]
    swf_length = struct.unpack_from("<I", redswf, tag_offset + 4)[0]
    if swf_length < 8 or swf_length > SWF_MAX_SIZE:
        return None
    expanded_limit = swf_length - 8 + CFX_TRAILER_MAX_SIZE + 1
    inflater = zlib.decompressobj()
    try:
        expanded = inflater.decompress(redswf[tag_offset + 8 : cfx_end], expanded_limit)
    except zlib.error:
        return None
    if (
        not inflater.eof
        or inflater.unused_data
        or inflater.unconsumed_tail
        or len(expanded) < swf_length - 8
        or len(expanded) > expanded_limit - 1
    ):
        return None
    swf_body = expanded[: swf_length - 8]
    trailer = expanded[swf_length - 8 :]
    try:
        swf = normalize_swf(
            b"FWS" + bytes((version,)) + struct.pack("<I", swf_length) + swf_body,
            "redswf payload",
        )
    except ProbeBuildError:
        return None
    return cfx_length, swf, trailer


def _locate_cfx_payload(
    redswf: bytes,
    chunk_offset: int,
    chunk_size: int,
) -> tuple[int, int, int, bytes, bytes]:
    """Locate the unique valid CFX block, ignoring magic bytes inside its zlib data."""
    chunk_end = chunk_offset + chunk_size
    search_from = chunk_offset
    selected: tuple[int, int, int, bytes, bytes] | None = None
    while True:
        tag_offset = redswf.find(b"CFX", search_from, chunk_end)
        if tag_offset < 0:
            break
        if tag_offset < chunk_offset + 4:
            search_from = tag_offset + 1
            continue
        decoded = _decode_cfx_candidate(redswf, tag_offset, chunk_end)
        if decoded is None:
            search_from = tag_offset + 1
            continue
        cfx_length, swf, trailer = decoded
        if selected is not None:
            raise ProbeBuildError(
                "redswf: multiple independent valid CFX payloads in the first chunk"
            )
        selected = (tag_offset - 4, tag_offset, cfx_length, swf, trailer)
        # Any further magic bytes before the declared CFX end are payload bytes,
        # not another resource signature.
        search_from = tag_offset + cfx_length
    if selected is None:
        raise ProbeBuildError(
            "redswf: no valid length-delimited CFX payload in the first chunk"
        )
    return selected


def patch_redswf(original: bytes, new_swf: bytes, trailer: bytes) -> bytes:
    new_swf = normalize_swf(new_swf, "compiled SWF")
    tables, chunk_offset, chunk_count = _read_chunk_table(original)
    chunks = [
        list(struct.unpack_from("<HHIIIII", original, chunk_offset + index * 24))
        for index in range(chunk_count)
    ]
    first_offset = chunks[0][4]
    first_size = chunks[0][3]
    cfx_start, cfx_position, old_cfx_length, _, _ = _locate_cfx_payload(
        original, first_offset, first_size
    )
    old_cfx_end = cfx_position + old_cfx_length

    cfx = (
        b"CFX"
        + new_swf[3:4]
        + struct.pack("<I", len(new_swf))
        + zlib.compress(new_swf[8:] + trailer, 9)
    )
    replacement = struct.pack("<I", len(cfx)) + cfx
    delta = len(replacement) - (4 + old_cfx_length)
    output = bytearray(original[:cfx_start] + replacement + original[old_cfx_end:])
    chunks[0][3] += delta
    for chunk in chunks[1:]:
        chunk[4] += delta
    chunk_bytes = b"".join(struct.pack("<HHIIIII", *chunk) for chunk in chunks)
    output[chunk_offset : chunk_offset + len(chunk_bytes)] = chunk_bytes
    tables[4][2] = zlib.crc32(chunk_bytes) & 0xFFFFFFFF
    struct.pack_into("<III", output, 0x28 + 4 * 12, *tables[4])
    struct.pack_into("<II", output, 0x18, len(output), len(output))

    packed = bytes(output)
    roundtrip_swf, roundtrip_trailer = unpack_redswf(packed)
    if roundtrip_swf != new_swf or roundtrip_trailer != trailer:
        raise ProbeBuildError(
            "redswf: compiled SWF or CFX trailer changed during packaging"
        )
    return packed


def verify_source_pins() -> tuple[bytes, bytes, bytes, bytes, str]:
    redswf_path = VANILLA / "gwint_game.redswf"
    swf_path = VANILLA / "gwint_game.swf"
    expected_files = {
        "gwint_game.redswf": redswf_path,
        "gwint_game.swf": swf_path,
        "ffdec-cli.jar": FFDEC_JAR,
        "vanilla-manifest.json": VANILLA_MANIFEST,
        "as3-manifest.json": AS3_MANIFEST,
    }
    observed = {name: sha256_file(path) for name, path in expected_files.items()}
    for name, expected in PINNED_INPUT_SHA256.items():
        if observed[name] != expected:
            raise ProbeBuildError(
                f"pinned input changed: {name} sha256={observed[name]}"
            )

    manifest = json.loads(VANILLA_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("format") != "witcher3-gwint-extraction-v1":
        raise ProbeBuildError("vanilla extraction manifest format is unsupported")
    assets = {asset.get("output"): asset for asset in manifest.get("assets", [])}
    for path, name in (
        (REDSWF_RELATIVE_PATH, "gwint_game.redswf"),
        (SWF_RELATIVE_PATH, "gwint_game.swf"),
    ):
        if assets.get(path, {}).get("sha256") != observed[name]:
            raise ProbeBuildError(
                f"vanilla manifest does not pin {path} to its local bytes"
            )

    as3_manifest = json.loads(AS3_MANIFEST.read_text(encoding="utf-8"))
    if as3_manifest.get("schema_version") != 1:
        raise ProbeBuildError("AS3 decompile manifest schema is unsupported")
    if as3_manifest.get("source_swf_sha256") != observed["gwint_game.swf"]:
        raise ProbeBuildError("AS3 decompile manifest refers to different SWF bytes")
    if as3_manifest.get("tool_sha256") != observed["ffdec-cli.jar"]:
        raise ProbeBuildError("AS3 decompile manifest refers to a different FFDec jar")
    as3_files = as3_manifest.get("files", {})
    for name in ("AIPlayerController", "BasePlayerController"):
        relative = f"scripts/{PACKAGE_PATH.as_posix()}/{name}.as"
        decompiled_path = ROOT / ".local/as3" / relative
        if as3_files.get(relative) != sha256_file(decompiled_path):
            raise ProbeBuildError(f"AS3 decompile manifest does not match {relative}")

    source_redswf = redswf_path.read_bytes()
    extracted_swf = swf_path.read_bytes()
    parsed_swf, _ = unpack_redswf(source_redswf)
    if parsed_swf != extracted_swf:
        raise ProbeBuildError(
            "extracted SWF does not match the installed redswf CFX payload"
        )
    ffdec = FFDEC_JAR.read_bytes()
    manifest_bytes = VANILLA_MANIFEST.read_bytes()
    return (
        source_redswf,
        extracted_swf,
        ffdec,
        manifest_bytes,
        observed["vanilla-manifest.json"],
    )


def _run_ffdec(jar: Path, log: list[str], *arguments: object, cwd: Path) -> None:
    command = [
        "java",
        "-Xmx1g",
        "-Djava.awt.headless=true",
        "-jar",
        str(jar),
        *(str(arg) for arg in arguments),
    ]
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=180, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeBuildError(f"FFDec command could not complete: {exc}") from exc
    output = result.stdout + result.stderr
    log.append("$ " + " ".join(command) + "\n" + output)
    if result.returncode != 0:
        raise ProbeBuildError(
            f"FFDec failed with status {result.returncode}: {output[-4000:]}"
        )


def _class_file(directory: Path, class_name: str) -> Path:
    result = directory / "scripts" / PACKAGE_PATH / f"{class_name}.as"
    if not result.is_file():
        raise ProbeBuildError(
            f"FFDec did not export expected class {PACKAGE}.{class_name}"
        )
    return result


def _inject_sources(
    original_ai: str, original_base: str, probe_sources: dict[str, str]
) -> dict[str, str]:
    if original_ai.count(AI_DECISION_ANCHOR) != 1:
        raise ProbeBuildError(
            "AIPlayerController: decision hook anchor is missing or ambiguous"
        )
    expected_classes = {"GwaiProbeChoice", "GwaiProbeTrace"}
    if set(probe_sources) != expected_classes:
        raise ProbeBuildError("probe helper source set is incomplete or ambiguous")
    for class_name, source in probe_sources.items():
        if not source.lstrip().startswith(f"internal class {class_name}"):
            raise ProbeBuildError(
                f"{class_name}: expected a package-scoped internal AS3 class"
            )

    patched_ai = require_exactly_once(
        original_ai,
        AI_IMPORT_ANCHOR,
        "\n   import flash.utils.ByteArray;\n   import flash.utils.Endian;\n   import red.core.events.GameEvent;",
        "AIPlayerController imports",
    )
    patched_ai = require_exactly_once(
        patched_ai,
        AI_DECISION_ANCHOR,
        AI_INJECTION,
        "AIPlayerController",
        before=True,
    )
    patched_base = require_exactly_once(
        original_base, BASE_APPLY_ANCHOR, BASE_INJECTION, "BasePlayerController"
    )
    package_closes = [
        index for index in range(len(patched_ai)) if patched_ai.startswith("\n}", index)
    ]
    if len(package_closes) != 1 or not patched_ai.rstrip().endswith("}"):
        raise ProbeBuildError(
            "AIPlayerController: package close is missing or ambiguous"
        )
    close_index = package_closes[0]
    helper_sources = "\n\n".join(
        probe_sources[name].strip() for name in sorted(probe_sources)
    )
    patched_ai = (
        patched_ai[:close_index] + "\n\n" + helper_sources + patched_ai[close_index:]
    )
    if (
        "CardTransaction" not in patched_ai
        or "GwaiProbeChoice.firstSupportedTransaction(this)" not in patched_ai
    ):
        raise ProbeBuildError(
            "AIPlayerController: deterministic CardTransaction injection is absent"
        )
    if patched_ai.index(
        "GwaiProbeChoice.firstSupportedTransaction(this)"
    ) > patched_ai.index("CalculateCardPowerPotentials();"):
        raise ProbeBuildError(
            "AIPlayerController: stock potential calculation precedes the probe choice"
        )
    if (
        patched_ai.count("internal class GwaiProbeChoice") != 1
        or patched_ai.count("internal class GwaiProbeTrace") != 1
    ):
        raise ProbeBuildError(
            "AIPlayerController: helper class injection is missing or duplicated"
        )
    if "GwaiProbeTrace.applyStarted" not in patched_base:
        raise ProbeBuildError("BasePlayerController: execution trace hook is absent")
    return {"AIPlayerController": patched_ai, "BasePlayerController": patched_base}


def _verify_compiled_sources(ai: str, base: str, choice: str, trace: str) -> None:
    checks = (
        (
            ai,
            "GwaiProbeChoice.firstSupportedTransaction(this)",
            "compiled decision hook",
        ),
        (ai, "GwaiProbeTrace.selected", "compiled selection trace"),
        (base, "GwaiProbeTrace.applyStarted", "compiled transaction apply-start trace"),
        (choice, "CardTransaction", "compiled CardTransaction selection"),
        (choice, "canBePlacedInSlot", "compiled legality guard"),
        (choice, "CardTemplate.CardEffect_None", "compiled no-special-effect filter"),
        (
            choice,
            "template.effectFlags[effectIndex]) != CardTemplate.CardEffect_None",
            "compiled explicit none-marker filter",
        ),
        (choice, "if(template.effectFlags != null)", "compiled empty-effect allowance"),
        (choice, "new CardTransaction()", "compiled fresh transaction construction"),
        (
            choice,
            "transaction.sourceCardInstanceRef = card",
            "compiled fresh transaction source",
        ),
        (
            choice,
            "transaction.targetPlayerID = controller.playerID",
            "compiled own-side target",
        ),
        (
            choice,
            "transaction.powerChangeResult = 0",
            "compiled diagnostic score initialization",
        ),
        (
            choice,
            "transaction.strategicValue = 0",
            "compiled diagnostic strategic-value initialization",
        ),
        (choice, "CARD_LIST_LOC_MELEE", "compiled explicit row order"),
        (trace, "JSON.stringify", "compiled full-precision JSON trace"),
        (trace, "ByteArray", "compiled binary64 capture"),
        (trace, "bytes.writeDouble(value)", "compiled binary64 write"),
        (trace, "power_change_bits", "compiled power Number bit field"),
        (trace, "strategic_value_bits", "compiled strategic Number bit field"),
        (trace, "metrics_source", "compiled metric provenance field"),
        (trace, "probe_placeholder", "compiled placeholder-metric marker"),
        (trace, "strategic_value", "compiled Number trace field"),
        (trace, "OnGwaiProbeTrace", "compiled recorder event"),
    )
    for source, needle, label in checks:
        if needle not in source:
            raise ProbeBuildError(f"compiled export is missing {label}: {needle}")
    if "getOptimalTransaction(" in choice:
        raise ProbeBuildError(
            "compiled selector reads the stock cached potential transaction"
        )
    if "CalculateCardPowerPotentials" in choice or "Math.random" in choice:
        raise ProbeBuildError(
            "compiled selector performs stock scoring or random row selection"
        )
    choice_index = ai.find("GwaiProbeChoice.firstSupportedTransaction(this)")
    potential_index = ai.find("CalculateCardPowerPotentials();")
    if choice_index < 0 or potential_index < 0 or choice_index > potential_index:
        raise ProbeBuildError(
            "compiled probe selection does not precede the stock potential calculation"
        )
    if "handleChoice" in choice or "OnGwaiProbeDecision" in trace:
        raise ProbeBuildError(
            "probe unexpectedly implements an unsupported choice response path"
        )


def _source_hashes() -> dict[str, str]:
    paths = {
        "GwaiProbeChoice.as": AS3_DIR / "GwaiProbeChoice.as",
        "GwaiProbeTrace.as": AS3_DIR / "GwaiProbeTrace.as",
        "gwai_probe_trace.ws": RECORDER_SOURCE,
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log: list[str] = []
    report: dict[str, Any] = {
        "format": "witcher3-gwent-offline-probe-v1",
        "status": "building",
        "installed": False,
        "runtime_verified": False,
        "witcher_script_engine_compiled": False,
        "external_ipc_proven": False,
        "choice_handling": "unsupported_fail_closed",
        "trace_schema": PROBE_SCHEMA,
        "vanilla_npc_deck_difficulty_max": 3,
        "cards_rules_rewards_or_decks_modified": False,
    }
    try:
        source_redswf, source_swf, ffdec_bytes, manifest_bytes, manifest_hash = (
            verify_source_pins()
        )
        source_hashes = _source_hashes()
        source_hashes["ffdec-cli.jar"] = sha256_bytes(ffdec_bytes)
        source_hashes["gwint_game.redswf"] = sha256_bytes(source_redswf)
        source_hashes["gwint_game.swf"] = sha256_bytes(source_swf)
        source_hashes["vanilla-manifest.json"] = manifest_hash
        source_hashes["as3-manifest.json"] = sha256_file(AS3_MANIFEST)
        report["inputs_sha256"] = source_hashes
        report["probe_contract"] = {
            "decision": "first supported plain creature with a fresh CardTransaction and first legal row in melee/ranged/siege order",
            "stock_potential_cache": "not read; the probe exits before CalculateCardPowerPotentials",
            "metrics_source": "probe_placeholder; zero scores do not reflect vanilla potential calculations",
            "execution": "the selected transaction reaches BasePlayerController.state_begin_ApplyingCard; this records apply-start, not effect completion",
            "number_serialization": "raw Number JSON plus 16-hex binary64 bit patterns; WitcherScript stores the JSON string as-is",
            "unsupported_follow_up_choice": "no response route; candidates requiring special handling are skipped",
            "transport": "trace event only; native external IPC is unproven",
        }

        with tempfile.TemporaryDirectory(prefix="witcher3-gwint-probe-") as temp_value:
            temp = Path(temp_value)
            source_path = temp / "gwint_game.swf"
            source_path.write_bytes(source_swf)
            export_path = temp / "export"
            _run_ffdec(
                FFDEC_JAR,
                log,
                "-selectclass",
                f"{PACKAGE}.AIPlayerController,{PACKAGE}.BasePlayerController",
                "-export",
                "script",
                export_path,
                source_path,
                cwd=temp,
            )
            original_ai = _class_file(export_path, "AIPlayerController").read_text(
                encoding="utf-8"
            )
            original_base = _class_file(export_path, "BasePlayerController").read_text(
                encoding="utf-8"
            )
            source_files = {
                "GwaiProbeChoice": (AS3_DIR / "GwaiProbeChoice.as").read_text(
                    encoding="utf-8"
                ),
                "GwaiProbeTrace": (AS3_DIR / "GwaiProbeTrace.as").read_text(
                    encoding="utf-8"
                ),
            }
            patched = _inject_sources(original_ai, original_base, source_files)
            import_root = temp / "import"
            import_package = import_root / PACKAGE_PATH
            import_package.mkdir(parents=True)
            for class_name, source in patched.items():
                (import_package / f"{class_name}.as").write_text(
                    source, encoding="utf-8"
                )

            compiled_swf_path = temp / "compiled.swf"
            _run_ffdec(
                FFDEC_JAR,
                log,
                "-importScript",
                source_path,
                compiled_swf_path,
                import_root,
                cwd=temp,
            )
            compiled_swf = normalize_swf(compiled_swf_path.read_bytes(), "FFDec output")

            verify_path = temp / "verify"
            _run_ffdec(
                FFDEC_JAR,
                log,
                "-export",
                "script",
                verify_path,
                compiled_swf_path,
                cwd=temp,
            )
            verified_ai = _class_file(verify_path, "AIPlayerController").read_text(
                encoding="utf-8"
            )
            verified_base = _class_file(verify_path, "BasePlayerController").read_text(
                encoding="utf-8"
            )
            verified_choice = _class_file(verify_path, "GwaiProbeChoice").read_text(
                encoding="utf-8"
            )
            verified_trace = _class_file(verify_path, "GwaiProbeTrace").read_text(
                encoding="utf-8"
            )
            _verify_compiled_sources(
                verified_ai, verified_base, verified_choice, verified_trace
            )

        original_trailer = unpack_redswf(source_redswf)[1]
        compiled_redswf = patch_redswf(source_redswf, compiled_swf, original_trailer)
        output_swf = OUTPUT_DIR / "gwint_game.swf"
        output_redswf = OUTPUT_DIR / "gwint_game.redswf"
        output_recorder = OUTPUT_DIR / "gwai_probe_trace.ws"
        output_swf.write_bytes(compiled_swf)
        output_redswf.write_bytes(compiled_redswf)
        output_recorder.write_bytes(RECORDER_SOURCE.read_bytes())
        if sha256_bytes(source_redswf) != PINNED_INPUT_SHA256["gwint_game.redswf"]:
            raise ProbeBuildError(
                "vanilla redswf changed while the offline build was running"
            )

        report.update(
            status="offline_compiled_and_export_verified",
            cfx_trailer_sha256=sha256_bytes(original_trailer),
            outputs_sha256={
                output_swf.name: sha256_file(output_swf),
                output_redswf.name: sha256_file(output_redswf),
                output_recorder.name: sha256_file(output_recorder),
            },
            output_directory=str(OUTPUT_DIR),
        )
        _write_json(OUTPUT_DIR / "manifest.json", report)
        manifest_digest = sha256_file(OUTPUT_DIR / "manifest.json")
        (OUTPUT_DIR / "manifest.sha256").write_text(
            f"{manifest_digest}  manifest.json\n", encoding="ascii"
        )
    except Exception as exc:
        report.update(status="build_failed", error=f"{type(exc).__name__}: {exc}")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _write_json(OUTPUT_DIR / "manifest.json", report)
        manifest_digest = sha256_file(OUTPUT_DIR / "manifest.json")
        (OUTPUT_DIR / "manifest.sha256").write_text(
            f"{manifest_digest}  manifest.json\n", encoding="ascii"
        )
        log.append(f"BUILD FAILED: {type(exc).__name__}: {exc}")
    finally:
        (OUTPUT_DIR / "build.log").write_text("\n\n".join(log) + "\n", encoding="utf-8")
    return report


def main() -> int:
    report = build()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "offline_compiled_and_export_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
