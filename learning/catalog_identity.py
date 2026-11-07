"""Portable identity of the gameplay inputs used by a policy."""

import hashlib
import json
from typing import Any


def catalog_identity(catalog: dict[str, Any]) -> str:
    """Hash schema, mappings, cards and decks, excluding local provenance paths."""
    content = {key: catalog[key] for key in ("schema_version", "cards", "decks")}
    content["runtime_mappings"] = catalog.get("runtime_mappings", {})
    payload = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
