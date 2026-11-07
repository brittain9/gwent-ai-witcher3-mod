"""Load and audit the installed Witcher 3 Gwent card catalog.

This module reads the local extraction produced by ``scripts/extract_game.py``.
It keeps the XML flag spellings beside the runtime values recovered from the
matching, locally decompiled ``CardTemplate.as``. It requires the JPEXS export
manifest to attest that the class came from the same Gwent SWF. It never embeds
card data.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GAME_DIR = Path.home() / ".local/share/Steam/steamapps/common/The Witcher 3"
DEFAULT_SOURCE_DIR = PACKAGE_ROOT / ".local/vanilla"
DEFAULT_CARD_TEMPLATE = (
    PACKAGE_ROOT / ".local/as3/scripts/red/game/witcher3/menus/gwint/CardTemplate.as"
)
DEFAULT_DECOMPILE_MANIFEST = PACKAGE_ROOT / ".local/as3/manifest.json"

CARD_FILES = (
    "gameplay/items/def_gwint_cards_final.xml",
    "gameplay/items/def_gwint_battle_king_cards.xml",
)
DECK_FILE = "gameplay/items/def_gwint_decks.xml"
CARD_TEMPLATE_SWF = "gameplay/gui_new/swf/gwint/gwint_game.swf"
CARD_TEMPLATE_REDSWF = "gameplay/gui_new/swf/gwint/gwint_game.redswf"
EXTRACTOR_FORMAT = "witcher3-gwint-extraction-v1"

_CONSTANT_RE = re.compile(
    r"public\s+static\s+const\s+(?P<name>[A-Za-z_]\w*)\s*:"
    r"\s*(?:u?int)\s*=\s*(?P<value>-?\d+)\s*;"
)

# These XML spellings do not normalize to their CardTemplate constant names.
# The targets are the shipped ActionScript enum constants, not inferred values.
_EFFECT_ALIASES = {
    "CP_COUNTER_KING_ABLILITY": "CardEffect_Counter_King",
    "CP_PICK_FOG_CARD": "CardEffect_Pick_Fog",
    "CP_PICK_FROST_CARD": "CardEffect_Pick_Frost",
    "CP_PICK_RAIN_CARD": "CardEffect_Pick_Rain",
    "CP_PICK_WEATHER_CARD": "CardEffect_Pick_Weather",
    "CP_RESURECT_CARD": "CardEffect_Resurect",
    "CP_RESURECT_FROM_ENEMY": "CardEffect_Resurect_Enemy",
    "CP_VIEW_3_ENEMY_CARDS": "CardEffect_View_3_Enemy",
}
_EXTRACTOR = None


class CatalogError(ValueError):
    """Raised when source provenance or catalog coverage is invalid."""


def _extractor() -> Any:
    """Load the sibling extractor so source hashes can be checked against bundles."""
    global _EXTRACTOR
    if _EXTRACTOR is not None:
        return _EXTRACTOR
    path = PACKAGE_ROOT / "scripts/extract_game.py"
    spec = importlib.util.spec_from_file_location("_witcher3_extract_game", path)
    if spec is None or spec.loader is None:
        raise CatalogError(f"cannot load bundle reader {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _EXTRACTOR = module
    return module


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold()).replace("x2", "2")


def _manifest_path(source_dir: Path) -> Path:
    return source_dir / "manifest.json"


def _load_json(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read JSON source {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogError(f"{path}: expected a JSON object")
    return value


def _resolve_manifest_source(
    game_dir: Path, source_dir: Path
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    manifest_path = _manifest_path(source_dir)
    manifest = _load_json(manifest_path)
    if manifest.get("format") != EXTRACTOR_FORMAT:
        raise CatalogError(
            f"{manifest_path}: expected extraction format {EXTRACTOR_FORMAT!r}"
        )
    expected_bundle_format = {
        "magic": "POTATO70",
        "version": 5,
    }
    if manifest.get("bundle_format") != expected_bundle_format:
        raise CatalogError(
            f"{manifest_path}: expected bundle format {expected_bundle_format}"
        )

    expected_game_dir = game_dir.resolve()
    recorded_game_dir = Path(str(manifest.get("source_game_dir", ""))).resolve()
    if recorded_game_dir != expected_game_dir:
        raise CatalogError(
            f"{manifest_path}: extracted from {recorded_game_dir}, "
            f"but requested game directory is {expected_game_dir}; re-extract first"
        )

    bundle_hashes: dict[str, str] = {}
    bundles = manifest.get("bundles")
    if not isinstance(bundles, dict):
        raise CatalogError(f"{manifest_path}: missing bundle provenance")
    for bundle_name in ("xml.bundle", "r4gui.bundle"):
        entry = bundles.get(bundle_name)
        if not isinstance(entry, dict):
            raise CatalogError(f"{manifest_path}: missing {bundle_name} provenance")
        bundle_path = game_dir / "content/content0/bundles" / bundle_name
        expected_source = Path(str(entry.get("source", ""))).resolve()
        if expected_source != bundle_path.resolve():
            raise CatalogError(
                f"{manifest_path}: {bundle_name} source path does not match "
                f"the requested game directory"
            )
        actual_hash = _sha256_file(bundle_path) if bundle_path.is_file() else None
        if actual_hash != entry.get("sha256"):
            raise CatalogError(
                f"{bundle_path}: SHA-256 differs from extraction manifest; "
                "re-extract before building the catalog"
            )
        if entry.get("version") != expected_bundle_format["version"]:
            raise CatalogError(
                f"{manifest_path}: {bundle_name} version does not match the supported format"
            )
        if entry.get("size") != bundle_path.stat().st_size:
            raise CatalogError(
                f"{manifest_path}: {bundle_name} size does not match source"
            )
        bundle_hashes[bundle_name] = actual_hash

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise CatalogError(f"{manifest_path}: missing extracted asset list")
    assets_by_output: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if isinstance(asset, dict) and isinstance(asset.get("output"), str):
            relative = asset["output"].replace("\\", "/")
            if relative in assets_by_output:
                raise CatalogError(
                    f"{manifest_path}: duplicate asset output {relative}"
                )
            assets_by_output[relative] = asset

    bundle_paths = {
        name: game_dir / "content/content0/bundles" / name
        for name in ("xml.bundle", "r4gui.bundle")
    }
    extractor = _extractor()
    bundle_entries: dict[str, dict[str, Any]] = {}
    for name, path in bundle_paths.items():
        entries: dict[str, Any] = {}
        for entry in extractor.parse_bundle(path):
            relative = entry.name.replace("\\", "/")
            if relative in entries:
                raise CatalogError(f"{path}: duplicate bundle member {relative}")
            entries[relative] = entry
        bundle_entries[name] = entries

    def verify_asset(relative: str, asset: dict[str, Any], expected_member: str) -> str:
        bundle_name = str(asset.get("source_bundle", ""))
        source_member = str(asset.get("source_member", "")).replace("\\", "/")
        if source_member != expected_member:
            raise CatalogError(
                f"{manifest_path}: {relative} points to unexpected member "
                f"{bundle_name}:{source_member}"
            )
        source_entry = bundle_entries.get(bundle_name, {}).get(source_member)
        if source_entry is None:
            raise CatalogError(
                f"{manifest_path}: {relative} points to missing {bundle_name}:{source_member}"
            )
        payload = extractor.read_entry(bundle_paths[bundle_name], source_entry)
        payload_hash = hashlib.sha256(payload).hexdigest()
        if payload_hash != asset.get("sha256"):
            raise CatalogError(
                f"{manifest_path}: {relative} hash does not match its bundled source"
            )
        output = source_dir / relative
        output_hash = _sha256_file(output) if output.is_file() else None
        if output_hash != payload_hash:
            raise CatalogError(
                f"{output}: SHA-256 differs from its bundled source; re-extract first"
            )
        return payload_hash

    asset_hashes: dict[str, str] = {}
    for relative in (*CARD_FILES, DECK_FILE):
        asset = assets_by_output.get(relative)
        if not asset or asset.get("source_bundle") != "xml.bundle":
            raise CatalogError(f"{manifest_path}: no XML bundle link for {relative}")
        asset_hashes[relative] = verify_asset(relative, asset, relative)

    swf_asset = assets_by_output.get(CARD_TEMPLATE_SWF)
    redswf_asset = assets_by_output.get(CARD_TEMPLATE_REDSWF)
    if (
        not swf_asset
        or swf_asset.get("source_bundle") != "r4gui.bundle"
        or swf_asset.get("derived_from", "").replace("\\", "/") != CARD_TEMPLATE_REDSWF
        or not redswf_asset
        or redswf_asset.get("source_bundle") != "r4gui.bundle"
    ):
        raise CatalogError(
            f"{manifest_path}: missing provenance link for the Gwent game SWF"
        )
    asset_hashes[CARD_TEMPLATE_REDSWF] = verify_asset(
        CARD_TEMPLATE_REDSWF, redswf_asset, CARD_TEMPLATE_REDSWF
    )
    swf_payload = extractor.extract_cfx_swf(
        (source_dir / CARD_TEMPLATE_REDSWF).read_bytes(), CARD_TEMPLATE_REDSWF
    )
    if hashlib.sha256(swf_payload).hexdigest() != swf_asset.get("sha256"):
        raise CatalogError(
            f"{manifest_path}: generated Gwent SWF does not match its redswf source"
        )
    extracted_swf = source_dir / CARD_TEMPLATE_SWF
    if not extracted_swf.is_file() or _sha256_file(extracted_swf) != swf_asset.get(
        "sha256"
    ):
        raise CatalogError(
            f"{extracted_swf}: SHA-256 differs from the SWF derived from its bundled source"
        )
    asset_hashes[CARD_TEMPLATE_SWF] = str(swf_asset["sha256"])

    return manifest, bundle_hashes, {"assets": asset_hashes, "manifest": manifest_path}


def _parse_runtime_constants(card_template: Path) -> dict[str, dict[str, int]]:
    try:
        source = card_template.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CatalogError(
            f"cannot read runtime enum source {card_template}: {exc}"
        ) from exc
    constants: dict[str, dict[str, int]] = {
        "FactionId": {},
        "CardType": {},
        "CardEffect": {},
    }
    for match in _CONSTANT_RE.finditer(source):
        name = match.group("name")
        for prefix in constants:
            marker = prefix + "_"
            if name.startswith(marker):
                constants[prefix][name] = int(match.group("value"))
                break
    for category in constants:
        if not constants[category]:
            raise CatalogError(
                f"{card_template}: no {category} constants found; "
                "expected the extracted CardTemplate.as"
            )
    return constants


def _validate_decompile_manifest(
    manifest_path: Path, card_template: Path, source_swf_sha256: str
) -> dict[str, str]:
    manifest = _load_json(manifest_path)
    if (
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
    ):
        raise CatalogError(
            f"{manifest_path}: expected decompile manifest schema_version 1"
        )
    if manifest.get("source_swf_sha256") != source_swf_sha256:
        raise CatalogError(
            f"{manifest_path}: decompile source SWF hash does not match the validated game SWF"
        )
    tool_hash = manifest.get("tool_sha256")
    if (
        not isinstance(tool_hash, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", tool_hash) is None
    ):
        raise CatalogError(f"{manifest_path}: missing valid JPEXS tool_sha256")
    try:
        relative_template = card_template.relative_to(manifest_path.parent).as_posix()
    except ValueError as exc:
        raise CatalogError(
            f"{card_template}: CardTemplate.as must be inside the attested export directory "
            f"{manifest_path.parent}"
        ) from exc
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise CatalogError(f"{manifest_path}: missing exported file hash map")
    expected_hash = files.get(relative_template)
    actual_hash = _sha256_file(card_template) if card_template.is_file() else None
    if actual_hash != expected_hash:
        raise CatalogError(
            f"{card_template}: SHA-256 does not match the decompile attestation "
            f"for {relative_template}"
        )
    return {
        "path": str(manifest_path),
        "sha256": _sha256_file(manifest_path),
        "tool_sha256": tool_hash,
        "source_swf_sha256": source_swf_sha256,
        "card_template_path": relative_template,
        "card_template_sha256": actual_hash,
    }


def _lookup_constant(
    token: str,
    token_prefix: str,
    constant_prefix: str,
    constants: dict[str, int],
    *,
    aliases: dict[str, str] | None = None,
) -> int | None:
    if aliases and token in aliases:
        target = aliases[token]
        return constants.get(target)
    stem = token.removeprefix(token_prefix + "_")
    if token_prefix == "EFFECT" and token.startswith("CP_"):
        stem = token.removeprefix("CP_")
    matches = [
        value
        for name, value in constants.items()
        if _canonical(name.removeprefix(constant_prefix + "_")) == _canonical(stem)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _parse_xml(path: Path) -> ET.Element:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise CatalogError(f"cannot parse XML source {path}: {exc}") from exc
    if root.tag != "redxml":
        raise CatalogError(f"{path}: expected root <redxml>, got <{root.tag}>")
    return root


def _int_attr(element: ET.Element, attr: str, path: Path) -> int:
    raw = element.get(attr)
    if raw is None:
        raise CatalogError(f"{path}: <{element.tag}> is missing required {attr!r}")
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise CatalogError(
            f"{path}: <{element.tag}> has invalid {attr}={raw!r}"
        ) from exc


def _flag_names(card: ET.Element, field: str) -> list[str]:
    container = card.find(field)
    if container is None:
        return []
    names: list[str] = []
    for flag in container.findall("flag"):
        name = flag.get("name")
        if not name:
            raise CatalogError(
                f"<card name={card.get('name')!r}> has a flag without a name"
            )
        names.append(name)
    return names


def _parse_cards(
    source_dir: Path,
    constants: dict[str, dict[str, int]],
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    cards: list[dict[str, Any]] = []
    ids: set[int] = set()
    names: set[str] = set()
    unknowns: list[str] = []

    for relative in CARD_FILES:
        path = source_dir / relative
        root = _parse_xml(path)
        elements = root.findall("./definitions/*/card")
        if not elements:
            raise CatalogError(f"{path}: no card definitions found")
        for element in elements:
            card_id = _int_attr(element, "index", path)
            power = _int_attr(element, "power", path)
            name = element.get("name")
            faction_symbol = element.get("faction_index")
            if not name:
                raise CatalogError(f"{path}: card index {card_id} has no name")
            if faction_symbol is None:
                raise CatalogError(f"{path}: card {name!r} has no faction_index")
            if card_id in ids:
                unknowns.append(f"duplicate card id {card_id} at {relative}")
            if name in names:
                unknowns.append(f"duplicate card name {name!r} at {relative}")
            ids.add(card_id)
            names.add(name)

            raw_types = _flag_names(element, "type_flags")
            raw_effects = _flag_names(element, "effect_flags")
            raw_summons = _flag_names(element, "summonFlags")
            type_values: list[int] = []
            effect_values: list[int] = []
            summon_values: list[int] = []

            for token in raw_types:
                value = _lookup_constant(
                    token, "TYPE", "CardType", constants["CardType"]
                )
                if value is None:
                    unknowns.append(
                        f"{relative}: card {name!r}: unknown type flag {token!r}"
                    )
                else:
                    type_values.append(value)
            for token in raw_effects:
                value = _lookup_constant(
                    token,
                    "EFFECT",
                    "CardEffect",
                    constants["CardEffect"],
                    aliases=_EFFECT_ALIASES,
                )
                if value is None:
                    unknowns.append(
                        f"{relative}: card {name!r}: unknown effect flag {token!r}"
                    )
                else:
                    effect_values.append(value)
            faction_value = _lookup_constant(
                faction_symbol, "F", "FactionId", constants["FactionId"]
            )
            if faction_value is None:
                unknowns.append(
                    f"{relative}: card {name!r}: unknown faction {faction_symbol!r}"
                )

            cards.append(
                {
                    "id": card_id,
                    "name": name,
                    "power": power,
                    "faction": faction_value,
                    # TYPE_KING marks the leader XML record, but the runtime
                    # CardTemplate typeArray for leaders is zero.
                    "types": 0
                    if raw_types and all(flag == "TYPE_KING" for flag in raw_types)
                    else _bitwise_or(type_values),
                    "effects": effect_values,
                    "summons": summon_values,
                    "raw_flags": {
                        "faction": faction_symbol,
                        "types": raw_types,
                        "effects": raw_effects,
                        "summons": raw_summons,
                    },
                    "coverage": {
                        "type_flags": {
                            "source": len(raw_types),
                            "mapped": len(type_values),
                        },
                        "effect_flags": {
                            "source": len(raw_effects),
                            "mapped": len(effect_values),
                            "state": (
                                "empty"
                                if not raw_effects
                                else "explicit_none"
                                if raw_effects == ["EFFECT_NONE"]
                                else "explicit"
                            ),
                        },
                        "summon_flags": {"source": len(raw_summons), "mapped": 0},
                    },
                    "_summon_names": raw_summons,
                    "_source": relative,
                }
            )
    by_name = {card["name"]: card["id"] for card in cards}
    for card in cards:
        resolved: list[int] = []
        for name in card.pop("_summon_names"):
            card_id = by_name.get(name)
            if card_id is None:
                unknowns.append(
                    f"{card['_source']}: card {card['name']!r}: unresolved summon {name!r}"
                )
            else:
                resolved.append(card_id)
        card["summons"] = resolved
        card["coverage"]["summon_flags"]["mapped"] = len(resolved)
        card.pop("_source")
    cards.sort(key=lambda card: card["id"])
    return cards, by_name, unknowns


def _bitwise_or(values: list[int]) -> int:
    result = 0
    for value in values:
        result |= value
    return result


def _parse_decks(
    source_dir: Path, cards_by_name: dict[str, int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = source_dir / DECK_FILE
    root = _parse_xml(path)
    collections = root.findall("./definitions/gwint_decks/deck_collection")
    if not collections:
        raise CatalogError(f"{path}: no deck collections found")

    decks: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for collection in collections:
        collection_name = collection.get("name")
        if not collection_name:
            raise CatalogError(f"{path}: deck collection has no name")
        dynamic_node = collection.find("dynamicCards")
        dynamic_cards: list[dict[str, Any]] = []
        if dynamic_node is not None:
            for card in dynamic_node.findall("card"):
                card_name = card.get("name")
                if card_name is None:
                    raise CatalogError(
                        f"{path}: dynamic card in {collection_name!r} has no name"
                    )
                difficulty = _int_attr(card, "difficulty", path)
                dynamic_cards.append({"name": card_name, "difficulty": difficulty})

        variants = collection.findall("deck")
        if not variants:
            raise CatalogError(
                f"{path}: collection {collection_name!r} has no variants"
            )
        for variant_index, variant in enumerate(variants):
            difficulty = _int_attr(variant, "difficulty", path)
            raw_leader = variant.get("leaderCard", "")
            raw_special = variant.get("specialCard", "")
            raw_cards: list[str] = []
            resolved_cards: list[int] = []
            dropped: list[str] = []
            for card in variant.findall("card"):
                name = card.get("name")
                if name is None:
                    raise CatalogError(
                        f"{path}: variant {collection_name!r}/{difficulty} has card without name"
                    )
                raw_cards.append(name)
                card_id = cards_by_name.get(name)
                if card_id is None:
                    dropped.append(name)
                    warnings.append(
                        {
                            "kind": "dropped_deck_card",
                            "collection": collection_name,
                            "difficulty": difficulty,
                            "name": name,
                            "reason": "name does not resolve to a card definition",
                        }
                    )
                else:
                    resolved_cards.append(card_id)

            def resolve_optional(field: str, name: str) -> int | None:
                if not name:
                    return None
                card_id = cards_by_name.get(name)
                if card_id is None:
                    dropped.append(name)
                    warnings.append(
                        {
                            "kind": f"dropped_deck_{field}",
                            "collection": collection_name,
                            "difficulty": difficulty,
                            "name": name,
                            "reason": "name does not resolve to a card definition",
                        }
                    )
                return card_id

            decks.append(
                {
                    "collection": collection_name,
                    "variant_index": variant_index,
                    "difficulty": difficulty,
                    "leader": resolve_optional("leader", raw_leader),
                    "special": resolve_optional("special", raw_special),
                    "cards": resolved_cards,
                    "raw_leader_card": raw_leader,
                    "raw_special_card": raw_special,
                    "raw_cards": raw_cards,
                    "dropped_cards": dropped,
                    "dynamic_cards": dynamic_cards,
                }
            )
    return decks, warnings


def _runtime_mappings(
    constants: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    return {
        "factions": {
            name.removeprefix("FactionId_"): value
            for name, value in constants["FactionId"].items()
        },
        "types": {
            name.removeprefix("CardType_"): value
            for name, value in constants["CardType"].items()
        },
        "effects": {
            name.removeprefix("CardEffect_"): value
            for name, value in constants["CardEffect"].items()
        },
    }


def load_catalog(
    game_dir: Path = DEFAULT_GAME_DIR,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    *,
    card_template: Path = DEFAULT_CARD_TEMPLATE,
    decompile_manifest: Path = DEFAULT_DECOMPILE_MANIFEST,
    manager_script: Path | None = None,
) -> dict[str, Any]:
    """Load, validate and return a JSON-serializable vanilla catalog.

    ``source_dir`` must be the extraction directory produced from ``game_dir``
    by ``scripts/extract_game.py``. Runtime enum numbers come from the extracted
    ``CardTemplate.as``. The generated catalog contains only local data and
    should be written under the ignored ``.runtime/`` or ``.local/`` directory.
    """
    game_dir = game_dir.expanduser().resolve()
    source_dir = source_dir.expanduser().resolve()
    card_template = card_template.expanduser().resolve()
    decompile_manifest = decompile_manifest.expanduser().resolve()
    manager_script = (
        manager_script.expanduser().resolve()
        if manager_script is not None
        else game_dir / "content/content0/scripts/game/gui/menus/gwintManager.ws"
    )

    manifest, bundle_hashes, extraction_sources = _resolve_manifest_source(
        game_dir, source_dir
    )
    decompile_provenance = _validate_decompile_manifest(
        decompile_manifest,
        card_template,
        extraction_sources["assets"][CARD_TEMPLATE_SWF],
    )
    constants = _parse_runtime_constants(card_template)
    manager_hash = _sha256_file(manager_script) if manager_script.is_file() else None
    if manager_hash is None:
        raise CatalogError(f"runtime schema source not found: {manager_script}")

    cards, cards_by_name, unknowns = _parse_cards(source_dir, constants)
    decks, warnings = _parse_decks(source_dir, cards_by_name)
    if unknowns:
        details = "\n  - ".join(unknowns)
        raise CatalogError(f"catalog has unmapped or duplicate data:\n  - {details}")

    source_hashes = {
        "xml.bundle": bundle_hashes["xml.bundle"],
        "r4gui.bundle": bundle_hashes["r4gui.bundle"],
        **extraction_sources["assets"],
        "CardTemplate.as": _sha256_file(card_template),
        "decompile-manifest.json": decompile_provenance["sha256"],
        "gwintManager.ws": manager_hash,
    }
    source_hashes = dict(sorted(source_hashes.items()))
    card_coverage = [
        {
            "id": card["id"],
            "name": card["name"],
            **card["coverage"],
            "complete": all(
                detail["source"] == detail["mapped"]
                for detail in card["coverage"].values()
            ),
        }
        for card in cards
    ]
    coverage = {
        "card_count": len(cards),
        "type_flag_count": sum(card["type_flags"]["source"] for card in card_coverage),
        "effect_flag_count": sum(
            card["effect_flags"]["source"] for card in card_coverage
        ),
        "summon_flag_count": sum(
            card["summon_flags"]["source"] for card in card_coverage
        ),
        "cards_with_empty_effects": sum(
            card["effect_flags"]["state"] == "empty" for card in card_coverage
        ),
        "cards_with_explicit_none": sum(
            card["effect_flags"]["state"] == "explicit_none" for card in card_coverage
        ),
        "per_card": card_coverage,
        "all_card_mappings_complete": all(card["complete"] for card in card_coverage),
    }
    return {
        "schema_version": 1,
        "source_hashes": source_hashes,
        "source_manifest": {
            "format": manifest["format"],
            "path": str(extraction_sources["manifest"]),
            "sha256": _sha256_file(extraction_sources["manifest"]),
            "source_game_dir": str(game_dir),
            "bundle_hashes": bundle_hashes,
            "game_swf_sha256": extraction_sources["assets"][CARD_TEMPLATE_SWF],
            "decompile": decompile_provenance,
        },
        "runtime_mappings": _runtime_mappings(constants),
        "cards": cards,
        "decks": decks,
        "warnings": warnings,
        "coverage": coverage,
    }
