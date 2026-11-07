"""Portable catalog identity and ActionScript inference-header serialization."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

AS3_CHUNK_CHARS = 48_000


def catalog_identity(catalog: dict[str, Any]) -> str:
    """Hash gameplay inputs, excluding installation and extraction provenance."""
    content = {key: catalog[key] for key in ("schema_version", "cards", "decks")}
    content["runtime_mappings"] = catalog.get("runtime_mappings", {})
    payload = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _as3_array(values: list[int]) -> str:
    return "[" + ",".join(str(value) for value in values) + "]"


def write_embed_header(
    path: Path, payload: bytes, metadata: dict[str, Any], class_name: str
) -> None:
    """Write the model parameters and locally supplied vocabulary for the runtime."""
    if not re.fullmatch(r"GwaiNetWeightsData(?:_[A-Za-z0-9]+)?", class_name):
        raise ValueError("unsafe ActionScript policy class name")
    encoded = base64.b64encode(payload).decode("ascii")
    chunks = [
        encoded[start : start + AS3_CHUNK_CHARS]
        for start in range(0, len(encoded), AS3_CHUNK_CHARS)
    ]
    offsets = metadata["offsets"]
    card_ids = metadata["card_ids"]
    card_effects = metadata["card_effects"]
    lines = [
        f"internal class {class_name}",
        "{",
        "   public static const EMBEDDED:Boolean = true;",
        f'   public static const CHECKPOINT_SHA256:String = "{metadata["checkpoint_sha256"]}";',
        f"   public static const UPDATE_COUNT:int = {metadata['update_count']};",
        f'   public static const EXPORT_SHA256:String = "{metadata["export_sha256"]}";',
        f'   public static const CATALOG_SHA256:String = "{metadata["catalog_sha256"]}";',
        f"   public static const WEIGHT_COUNT:int = {metadata['parameter_count']};",
        f"   public static const OBSERVATION_SIZE:int = {metadata['observation_size']};",
        f"   public static const CARD_COUNT:int = {metadata['card_count']};",
        f"   public static const WIDTH:int = {metadata['width']};",
        f"   public static const BLOCKS:int = {metadata['blocks']};",
        "   public static const OFFSETS:Object = {",
    ]
    offset_items = list(offsets.items())
    for index, (name, offset) in enumerate(offset_items):
        suffix = "," if index + 1 < len(offset_items) else ""
        lines.append(f'      "{name}":{offset}{suffix}')
    lines.extend(
        ["   };", f"   public static const CARD_IDS:Array = {_as3_array(card_ids)};"]
    )
    lines.append(
        "   public static const CARD_EFFECTS:Array = ["
        + ",".join(_as3_array(effects) for effects in card_effects)
        + "];"
    )
    lines.append("   public static const BASE64:Array = [")
    lines.extend(
        f'      "{chunk}"' + ("," if index + 1 < len(chunks) else "")
        for index, chunk in enumerate(chunks)
    )
    lines.extend(["   ];", "}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
