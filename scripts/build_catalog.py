#!/usr/bin/env python3
"""Build a version-pinned, audited JSON catalog from local game sources."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from w3gwent.catalog import (  # noqa: E402
    DEFAULT_CARD_TEMPLATE,
    DEFAULT_DECOMPILE_MANIFEST,
    DEFAULT_GAME_DIR,
    DEFAULT_SOURCE_DIR,
    CatalogError,
    load_catalog,
)

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / ".runtime/catalog.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=DEFAULT_GAME_DIR,
        help="installed Witcher 3 directory",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="validated extraction directory (default: .local/vanilla)",
    )
    parser.add_argument(
        "--card-template",
        type=Path,
        default=DEFAULT_CARD_TEMPLATE,
        help="locally decompiled CardTemplate.as enum source",
    )
    parser.add_argument(
        "--decompile-manifest",
        type=Path,
        default=DEFAULT_DECOMPILE_MANIFEST,
        help="JPEXS export manifest that ties CardTemplate.as to the source SWF",
    )
    parser.add_argument(
        "--manager-script",
        type=Path,
        default=None,
        help="installed gwintManager.ws source (defaults under --game-dir)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="output JSON path (default: .runtime/catalog.json)",
    )
    args = parser.parse_args(argv)

    try:
        catalog = load_catalog(
            args.game_dir,
            args.source_dir,
            card_template=args.card_template,
            decompile_manifest=args.decompile_manifest,
            manager_script=args.manager_script,
        )
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp")
        temporary.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    except (OSError, CatalogError) as exc:
        print(f"build_catalog: {exc}", file=sys.stderr)
        return 2

    print(
        f"Wrote {len(catalog['cards'])} cards and {len(catalog['decks'])} deck variants "
        f"to {output}; {len(catalog['warnings'])} explicit deck warnings"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
