#!/usr/bin/env python3
"""Extract installed Witcher 3 Gwent screens and XML for local inspection."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAGIC = b"POTATO70"
PREAMBLE_SIZE = 32
ENTRY_SIZE = 304
SUPPORTED_VERSION = 5
MAX_BUNDLE_ENTRY_SIZE = 512 * 1024 * 1024
SWF_MAX_SIZE = 256 * 1024 * 1024
CFX_TRAILER_MAX_SIZE = 4 * 1024 * 1024
DEFAULT_GAME_DIR = Path.home() / ".local/share/Steam/steamapps/common/The Witcher 3"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / ".local/vanilla"
GAME_SWFS = {
    "gameplay/gui_new/swf/gwint/gwint_game.redswf",
    "gameplay/gui_new/swf/gwint/deck_builder.redswf",
}
REQUIRED_XML = {
    f"gameplay/items/{filename}"
    for filename in (
        "def_gwint_cards_final.xml",
        "def_gwint_battle_king_cards.xml",
        "def_gwint_king_cards.xml",
        "def_gwint_decks.xml",
        "def_item_gwint.xml",
    )
}


class ExtractionError(ValueError):
    """Raised when a bundle or one of its selected resources is malformed."""


@dataclass(frozen=True)
class BundleEntry:
    name: str
    offset: int
    unpacked_length: int
    packed_length: int
    crc32: int
    compression: int


def safe_member_path(member: str) -> PurePosixPath:
    """Normalize a bundle path and reject names that can escape the output dir."""
    if not member or "\0" in member:
        raise ExtractionError(
            f"invalid empty or NUL-containing bundle path: {member!r}"
        )
    normalized = member.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ExtractionError(f"absolute bundle path is not allowed: {member!r}")
    path = PurePosixPath(normalized)
    if any(part in ("", ".", "..") for part in normalized.split("/")):
        raise ExtractionError(f"unsafe bundle path: {member!r}")
    return path


def parse_bundle(path: Path) -> list[BundleEntry]:
    """Read and validate the measured POTATO70/version 5 bundle index."""
    size = path.stat().st_size
    with path.open("rb") as bundle:
        preamble = bundle.read(PREAMBLE_SIZE)
        if len(preamble) != PREAMBLE_SIZE:
            raise ExtractionError(f"{path}: truncated bundle preamble")
        if preamble[:8] != MAGIC:
            raise ExtractionError(f"{path}: expected {MAGIC!r} magic")
        header_size = struct.unpack_from("<I", preamble, 16)[0]
        version = struct.unpack_from("<H", preamble, 20)[0]
        if version != SUPPORTED_VERSION:
            raise ExtractionError(
                f"{path}: bundle version {version} is unsupported; only version 5 is measured"
            )
        if header_size % ENTRY_SIZE:
            raise ExtractionError(
                f"{path}: index size {header_size} is not a multiple of {ENTRY_SIZE}"
            )
        index_end = PREAMBLE_SIZE + header_size
        if index_end > size:
            raise ExtractionError(
                f"{path}: truncated bundle index ({index_end} bytes required)"
            )

        entries: list[BundleEntry] = []
        bundle.seek(PREAMBLE_SIZE)
        for index in range(header_size // ENTRY_SIZE):
            raw = bundle.read(ENTRY_SIZE)
            if len(raw) != ENTRY_SIZE:
                raise ExtractionError(f"{path}: truncated index entry {index}")
            name_bytes = raw[:256].split(b"\0", 1)[0]
            try:
                name = name_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ExtractionError(
                    f"{path}: entry {index} has an invalid UTF-8 path"
                ) from exc
            safe_member_path(name)
            low_offset, high_offset, unpacked, packed, crc32, compression = (
                struct.unpack_from("<IIIIII", raw, 272)
            )
            offset = low_offset | (high_offset << 32)
            if compression not in (0, 1):
                raise ExtractionError(
                    f"{path}: {name}: unsupported compression {compression}"
                )
            if compression == 0 and packed != unpacked:
                raise ExtractionError(
                    f"{path}: {name}: uncompressed entry lengths differ"
                )
            if offset < index_end or offset > size or packed > size - offset:
                raise ExtractionError(
                    f"{path}: {name}: entry data lies outside bundle bounds"
                )
            entries.append(
                BundleEntry(name, offset, unpacked, packed, crc32, compression)
            )
    return entries


def read_entry(bundle_path: Path, entry: BundleEntry) -> bytes:
    """Read one indexed entry with bounded zlib expansion and integrity checks."""
    if entry.unpacked_length > MAX_BUNDLE_ENTRY_SIZE:
        raise ExtractionError(
            f"{bundle_path}: {entry.name}: declared entry length {entry.unpacked_length} exceeds limit"
        )
    with bundle_path.open("rb") as bundle:
        bundle.seek(entry.offset)
        packed = bundle.read(entry.packed_length)
    if len(packed) != entry.packed_length:
        raise ExtractionError(f"{bundle_path}: {entry.name}: truncated entry data")
    if entry.compression == 0:
        data = packed
    else:
        inflater = zlib.decompressobj()
        try:
            data = inflater.decompress(packed, entry.unpacked_length + 1)
        except zlib.error as exc:
            raise ExtractionError(
                f"{bundle_path}: {entry.name}: invalid zlib stream: {exc}"
            ) from exc
        if len(data) > entry.unpacked_length:
            raise ExtractionError(
                f"{bundle_path}: {entry.name}: decompressed data exceeds declared length"
            )
        if not inflater.eof:
            raise ExtractionError(
                f"{bundle_path}: {entry.name}: incomplete zlib stream"
            )
        if inflater.unconsumed_tail:
            raise ExtractionError(
                f"{bundle_path}: {entry.name}: zlib output exceeds declared length"
            )
        if inflater.unused_data:
            raise ExtractionError(
                f"{bundle_path}: {entry.name}: trailing bytes after zlib stream"
            )
    if len(data) != entry.unpacked_length:
        raise ExtractionError(
            f"{bundle_path}: {entry.name}: expected {entry.unpacked_length} bytes, got {len(data)}"
        )
    actual_crc = zlib.crc32(data) & 0xFFFFFFFF
    if actual_crc != entry.crc32:
        raise ExtractionError(
            f"{bundle_path}: {entry.name}: CRC32 mismatch, expected {entry.crc32:08x}, got {actual_crc:08x}"
        )
    return data


def extract_cfx_swf(redswf: bytes, label: str = "redswf") -> bytes:
    """Unwrap a CR2W CSwfResource's CFX payload as a standard uncompressed SWF."""
    cfx_offset = redswf.find(b"CFX")
    if cfx_offset < 0 or len(redswf) < cfx_offset + 8:
        raise ExtractionError(f"{label}: CFX header not found")
    header = redswf[cfx_offset : cfx_offset + 8]
    version = header[3]
    declared_length = struct.unpack_from("<I", header, 4)[0]
    if declared_length < 8 or declared_length > SWF_MAX_SIZE:
        raise ExtractionError(f"{label}: unreasonable SWF length {declared_length}")
    expected_body_length = declared_length - 8
    inflater = zlib.decompressobj()
    try:
        expanded = inflater.decompress(
            redswf[cfx_offset + 8 :], expected_body_length + CFX_TRAILER_MAX_SIZE + 1
        )
    except zlib.error as exc:
        raise ExtractionError(f"{label}: invalid CFX zlib stream: {exc}") from exc
    if not inflater.eof:
        raise ExtractionError(f"{label}: incomplete CFX zlib stream")
    if inflater.unconsumed_tail:
        raise ExtractionError(f"{label}: CFX expands beyond the allowed trailer size")
    if len(expanded) > expected_body_length + CFX_TRAILER_MAX_SIZE:
        raise ExtractionError(f"{label}: CFX expands beyond the allowed trailer size")
    if len(expanded) < expected_body_length:
        raise ExtractionError(
            f"{label}: expected at least {expected_body_length} SWF body bytes, got {len(expanded)}"
        )
    body = expanded[:expected_body_length]
    if not body.endswith(b"\x00\x00"):
        raise ExtractionError(f"{label}: decompressed SWF is missing its End tag")
    return b"FWS" + bytes((version,)) + struct.pack("<I", declared_length) + body


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def matching_xml(entry: BundleEntry) -> bool:
    path = safe_member_path(entry.name)
    lower = str(path).lower()
    return path.suffix.lower() == ".xml" and ("gwint" in lower or "gwent" in lower)


def output_path_for(output_dir: Path, member: PurePosixPath) -> Path:
    path = output_dir.joinpath(*member.parts)
    if not path.resolve().is_relative_to(output_dir.resolve()):
        raise ExtractionError(
            f"output path escapes extraction directory through a symlink: {path}"
        )
    return path


def extract(game_dir: Path, output_dir: Path) -> dict[str, object]:
    bundles_dir = game_dir / "content/content0/bundles"
    selected_bundles = {
        "r4gui.bundle": GAME_SWFS,
        "xml.bundle": None,
    }
    manifest_assets: list[dict[str, object]] = []
    bundle_metadata: dict[str, dict[str, object]] = {}

    for bundle_name, exact_members in selected_bundles.items():
        bundle_path = bundles_dir / bundle_name
        if not bundle_path.is_file():
            raise ExtractionError(f"required bundle not found: {bundle_path}")
        entries = parse_bundle(bundle_path)
        bundle_bytes = bundle_path.read_bytes()
        bundle_metadata[bundle_name] = {
            "source": str(bundle_path),
            "size": len(bundle_bytes),
            "sha256": sha256(bundle_bytes),
            "version": SUPPORTED_VERSION,
        }
        selected = [
            entry
            for entry in entries
            if (
                entry.name.replace("\\", "/") in exact_members
                if exact_members is not None
                else matching_xml(entry)
            )
        ]
        found = {entry.name.replace("\\", "/") for entry in selected}
        required = exact_members if exact_members is not None else REQUIRED_XML
        missing = required - found
        if missing:
            raise ExtractionError(
                f"{bundle_name}: required resources not found: {', '.join(sorted(missing))}"
            )

        for entry in selected:
            resource = read_entry(bundle_path, entry)
            member_path = safe_member_path(entry.name)
            output_path = output_path_for(output_dir, member_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resource)
            manifest_assets.append(
                {
                    "source_bundle": bundle_name,
                    "source_member": entry.name,
                    "output": output_path.relative_to(output_dir).as_posix(),
                    "size": len(resource),
                    "crc32": f"{entry.crc32:08x}",
                    "sha256": sha256(resource),
                }
            )
            if entry.name.replace("\\", "/") in GAME_SWFS:
                swf = extract_cfx_swf(resource, entry.name)
                swf_path = output_path_for(output_dir, member_path.with_suffix(".swf"))
                swf_path.write_bytes(swf)
                manifest_assets.append(
                    {
                        "source_bundle": bundle_name,
                        "source_member": entry.name,
                        "derived_from": output_path.relative_to(output_dir).as_posix(),
                        "output": swf_path.relative_to(output_dir).as_posix(),
                        "format": "FWS",
                        "size": len(swf),
                        "sha256": sha256(swf),
                    }
                )

    manifest: dict[str, object] = {
        "format": "witcher3-gwint-extraction-v1",
        "bundle_format": {"magic": MAGIC.decode(), "version": SUPPORTED_VERSION},
        "source_game_dir": str(game_dir),
        "bundles": bundle_metadata,
        "assets": sorted(manifest_assets, key=lambda asset: str(asset["output"])),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_path_for(output_dir, PurePosixPath("manifest.json"))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=DEFAULT_GAME_DIR,
        help="installed Witcher 3 directory",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="extraction directory"
    )
    args = parser.parse_args(argv)
    try:
        manifest = extract(
            args.game_dir.expanduser().resolve(), args.output.expanduser().resolve()
        )
    except (OSError, ExtractionError) as exc:
        print(f"extract_game: {exc}", file=sys.stderr)
        return 2
    print(
        f"Extracted {len(manifest['assets'])} resources to {args.output.expanduser().resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
