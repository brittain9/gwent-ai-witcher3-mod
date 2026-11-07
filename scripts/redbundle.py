#!/usr/bin/env python3
"""Write and read the two files that let Witcher 3 load a mod's resources.

The game reads non-script resources from ``<mod>/content/*.bundle`` files listed in
``<mod>/content/metadata.store``; it reads loose resource files only with the developer
``-loose`` switch. The layouts below were measured on the Remastered 5.01 install
(``content/metadata.store`` VTM version 7, ``r4gui.bundle`` POTATO70 version 5):

Bundle (POTATO70 v5)
    32-byte preamble: magic, u32 file size, u32 0, u32 index size, u16 version 5,
    u32 data offset, 6 zero bytes. Then one 304-byte index entry per file: 256-byte
    NUL-padded path, 16 bytes (zero here; vanilla holds an unidentified hash), u32 offset
    low, u32 offset high, u32 unpacked size, u32 packed size, u32 CRC-32 of the unpacked
    bytes, u32 compression (0 none, 1 zlib), 8 zero bytes. Payloads start on 16-byte
    boundaries and the file is padded to a multiple of 16 bytes.

Metadata store (VTM v7)
    ``\\x03VTM``, u32 version 7, u32 largest packed size and u32 largest unpacked size of
    the compressed files. Then, each as a variable-length count plus records: the string
    table; file infos (32 bytes); file entries (24 bytes); bundles (24 bytes); buffers
    (u32); directories (8 bytes); file names (12 bytes); path hashes (16 bytes, sorted by
    FNV-1a 64 of the path). Index 0 of the file info, file entry and bundle tables is an
    all-zero record. Paths use backslashes.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

BUNDLE_MAGIC = b"POTATO70"
BUNDLE_VERSION = 5
BUNDLE_PREAMBLE_SIZE = 32
BUNDLE_ENTRY_SIZE = 304
BUNDLE_PATH_SIZE = 256
STORE_MAGIC = b"\x03VTM"
STORE_VERSION = 7
BUNDLE_NAME = "blob0.bundle"
COMPRESSION_NONE = 0
COMPRESSION_ZLIB = 1
EMPTY_BUFFER_COUNT = b"\x80"


class BundleFormatError(ValueError):
    """Raised when bundle or metadata-store bytes do not follow the measured layout."""


@dataclass(frozen=True)
class StoreFile:
    path: str
    packed_size: int
    unpacked_size: int
    compression: int
    offset: int
    bundle: str


@dataclass(frozen=True)
class Store:
    max_packed_size: int
    max_unpacked_size: int
    files: tuple[StoreFile, ...]
    bundles: tuple[tuple[str, int, int], ...]  # (name, data size, header size)


def fnv1a64(data: bytes) -> int:
    value = 0xCBF29CE484222325
    for byte in data:
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def _encode_count(value: int) -> bytes:
    """Encode a non-negative count: 6 value bits and a continue bit, then 7-bit groups."""
    if value < 0:
        raise BundleFormatError(f"negative count: {value}")
    first = value & 0x3F
    value >>= 6
    out = bytearray([first | (0x40 if value else 0)])
    while value:
        part = value & 0x7F
        value >>= 7
        out.append(part | (0x80 if value else 0))
    return bytes(out)


def _decode_count(data: bytes, position: int) -> tuple[int, int]:
    if position >= len(data):
        raise BundleFormatError("metadata store ends inside a count")
    byte = data[position]
    position += 1
    value = byte & 0x3F
    negative = byte & 0x80
    more = byte & 0x40
    shift = 6
    while more:
        if position >= len(data):
            raise BundleFormatError("metadata store ends inside a count")
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        more = byte & 0x80
    if negative and value:
        raise BundleFormatError("metadata store holds a negative count")
    return value, position


def _normalize_path(path: str) -> str:
    normalized = path.replace("/", "\\")
    parts = normalized.split("\\")
    if (
        not normalized
        or normalized != normalized.lower()
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise BundleFormatError(
            f"bundle paths must be lower-case relative paths: {path!r}"
        )
    if len(normalized.encode("utf-8")) >= BUNDLE_PATH_SIZE:
        raise BundleFormatError(f"bundle path is too long: {path!r}")
    return normalized


def pack(files: list[tuple[str, bytes]], *, level: int = 9) -> tuple[bytes, bytes]:
    """Return (``blob0.bundle`` bytes, ``metadata.store`` bytes) holding ``files`` zlib-packed."""
    if not files:
        raise BundleFormatError("a bundle needs at least one file")
    paths = [_normalize_path(path) for path, _ in files]
    if len(set(paths)) != len(paths):
        raise BundleFormatError("duplicate bundle paths")
    data_offset = BUNDLE_PREAMBLE_SIZE + BUNDLE_ENTRY_SIZE * len(files)
    index = bytearray()
    body = bytearray()
    records: list[tuple[str, int, int, int]] = []  # path, offset, packed, unpacked
    for path, (_, data) in zip(paths, files):
        payload = zlib.compress(data, level)
        offset = data_offset + len(body)
        entry = bytearray(BUNDLE_ENTRY_SIZE)
        entry[: len(path)] = path.encode("utf-8")
        struct.pack_into(
            "<IIIIII",
            entry,
            272,
            offset & 0xFFFFFFFF,
            offset >> 32,
            len(data),
            len(payload),
            zlib.crc32(data) & 0xFFFFFFFF,
            COMPRESSION_ZLIB,
        )
        index += entry
        body += payload + b"\0" * (-len(payload) % 16)
        records.append((path, offset, len(payload), len(data)))
    file_size = data_offset + len(body)
    preamble = (
        BUNDLE_MAGIC
        + struct.pack("<III", file_size, 0, len(index))
        + struct.pack("<HI", BUNDLE_VERSION, data_offset)
        + b"\0" * 6
    )
    bundle = preamble + bytes(index) + bytes(body)
    store = _store_bytes(records, data_size=len(body), header_size=data_offset)
    return bundle, store


def _store_bytes(
    records: list[tuple[str, int, int, int]], *, data_size: int, header_size: int
) -> bytes:
    strings = bytearray(b"\0")
    bundle_name_offset = len(strings)
    strings += BUNDLE_NAME.encode("ascii") + b"\0"
    path_offsets = []
    for path, *_ in records:
        path_offsets.append(len(strings))
        strings += path.encode("utf-8") + b"\0"
    directories = [(len(strings), 0)]
    strings += b"\0"
    directory_index: dict[tuple[str, ...], int] = {(): 0}
    for path, *_ in records:
        parts = path.split("\\")[:-1]
        for depth in range(1, len(parts) + 1):
            key = tuple(parts[:depth])
            if key not in directory_index:
                directory_index[key] = len(directories)
                directories.append((len(strings), directory_index[key[:-1]]))
                strings += parts[depth - 1].encode("utf-8") + b"\0"

    out = bytearray(STORE_MAGIC)
    out += struct.pack(
        "<III", STORE_VERSION, max(r[2] for r in records), max(r[3] for r in records)
    )
    out += _encode_count(len(strings)) + strings
    out += _encode_count(len(records) + 1) + b"\0" * 32
    for number, (path_offset, (_, _, packed, unpacked)) in enumerate(
        zip(path_offsets, records), start=1
    ):
        out += struct.pack(
            "<8I", path_offset, 0, packed, unpacked, number, 0, 0, COMPRESSION_ZLIB
        )
    out += _encode_count(len(records) + 1) + b"\0" * 24
    for number, (_, offset, packed, _) in enumerate(records, start=1):
        out += struct.pack(
            "<6I", offset & 0xFFFFFFFF, offset >> 32, packed, 0, number, 1
        )
    out += _encode_count(2) + b"\0" * 24
    out += struct.pack(
        "<QIIII", data_size, header_size, bundle_name_offset, 1, len(records)
    )
    # No mesh buffers. Written as 0x80 (sign bit, value 0), the byte Better Gwent's stores carry; those
    # stores loaded in the game on 5.01, and this makes the whole store byte-identical to theirs.
    out += EMPTY_BUFFER_COUNT
    out += _encode_count(len(directories)) + b"".join(
        struct.pack("<II", *item) for item in directories
    )
    out += _encode_count(len(records))
    for number, (path_offset, (path, *_)) in enumerate(
        zip(path_offsets, records), start=1
    ):
        parts = path.split("\\")
        name_offset = path_offset + len(path) - len(parts[-1])
        out += struct.pack(
            "<III", number, directory_index[tuple(parts[:-1])], name_offset
        )
    hashes = sorted(
        (fnv1a64(path.encode("utf-8")), number)
        for number, (path, *_) in enumerate(records, start=1)
    )
    out += _encode_count(len(hashes)) + b"".join(
        struct.pack("<QIi", value, number, 0) for value, number in hashes
    )
    return bytes(out)


def read_store(data: bytes) -> Store:
    """Parse a metadata store strictly: every table, every cross-reference, and exact length."""
    if data[:4] != STORE_MAGIC:
        raise BundleFormatError("not a metadata store (magic)")
    version, max_packed, max_unpacked = struct.unpack_from("<III", data, 4)
    if version != STORE_VERSION:
        raise BundleFormatError(
            f"metadata store version {version}; only {STORE_VERSION} is measured"
        )
    position = 16

    def table(width: int, layout: str) -> list[tuple[int, ...]]:
        nonlocal position
        count, position = _decode_count(data, position)
        end = position + count * width
        if end > len(data):
            raise BundleFormatError("metadata store table runs past the end")
        rows = [
            struct.unpack_from(layout, data, position + row * width)
            for row in range(count)
        ]
        position = end
        return rows

    size, position = _decode_count(data, position)
    strings = data[position : position + size]
    if len(strings) != size:
        raise BundleFormatError("metadata store string table runs past the end")
    position += size

    def text(offset: int) -> str:
        end = strings.find(b"\0", offset)
        if offset >= len(strings) or end < 0:
            raise BundleFormatError(
                f"string offset {offset} is outside the string table"
            )
        return strings[offset:end].decode("utf-8")

    infos = table(32, "<8I")
    entries = table(24, "<6I")
    bundles = table(24, "<QIIII")
    buffers = table(4, "<I")
    directories = table(8, "<II")
    names = table(12, "<III")
    hashes = table(16, "<QIi")
    if position != len(data):
        raise BundleFormatError(
            f"metadata store has {len(data) - position} trailing bytes"
        )
    for label, rows in (
        ("file info", infos),
        ("file entry", entries),
        ("bundle", bundles),
    ):
        if not rows or any(rows[0]):
            raise BundleFormatError(f"{label} table must start with an all-zero record")
    if buffers:
        raise BundleFormatError("mesh buffers are not supported in a mod store")
    bundle_rows = [(text(row[2]), row[0], row[1]) for row in bundles[1:]]
    files = []
    for number, info in enumerate(infos[1:], start=1):
        path_offset, _, packed, unpacked, first_entry, _, _, compression = info
        if not 0 < first_entry < len(entries):
            raise BundleFormatError(f"file info {number} points at a missing entry")
        offset_low, offset_high, entry_packed, _, owner, bundle_index = entries[
            first_entry
        ]
        if (
            owner != number
            or entry_packed != packed
            or not 0 < bundle_index < len(bundles)
        ):
            raise BundleFormatError(
                f"file entry {first_entry} does not match file info {number}"
            )
        files.append(
            StoreFile(
                text(path_offset),
                packed,
                unpacked,
                compression,
                offset_low | (offset_high << 32),
                bundle_rows[bundle_index - 1][0],
            )
        )
    expected_hashes = sorted(
        (fnv1a64(item.path.encode("utf-8")), number)
        for number, item in enumerate(files, start=1)
    )
    if [(value, number) for value, number, _ in hashes] != expected_hashes or any(
        row[2] for row in hashes
    ):
        raise BundleFormatError("path hash table does not match the file paths")
    if len(names) != len(files):
        raise BundleFormatError("file name table does not cover every file")
    for number, directory, name_offset in names:
        if not 0 < number <= len(files) or not 0 <= directory < len(directories):
            raise BundleFormatError("file name record points outside the tables")
        parts: list[str] = []
        current = directory
        for _ in range(len(directories)):
            if current == 0:
                break
            name_offset_dir, parent = directories[current]
            parts.append(text(name_offset_dir))
            current = parent
        else:
            raise BundleFormatError("directory table has a cycle")
        rebuilt = "\\".join([*reversed(parts), text(name_offset)])
        if rebuilt != files[number - 1].path:
            raise BundleFormatError(
                f"directory chain {rebuilt!r} differs from {files[number - 1].path!r}"
            )
    return Store(max_packed, max_unpacked, tuple(files), tuple(bundle_rows))


def bundle_member_names(path: Path) -> list[str]:
    """List a bundle's member paths from its index without decoding payloads.

    Used to find other mods that ship the same resource; it accepts any compression type.
    """
    with path.open("rb") as handle:
        preamble = handle.read(BUNDLE_PREAMBLE_SIZE)
        if len(preamble) != BUNDLE_PREAMBLE_SIZE or preamble[:8] != BUNDLE_MAGIC:
            raise BundleFormatError(f"{path}: not a POTATO70 bundle")
        index_size = struct.unpack_from("<I", preamble, 16)[0]
        if index_size % BUNDLE_ENTRY_SIZE:
            raise BundleFormatError(
                f"{path}: index size {index_size} is not a multiple of {BUNDLE_ENTRY_SIZE}"
            )
        index = handle.read(index_size)
    if len(index) != index_size:
        raise BundleFormatError(f"{path}: truncated bundle index")
    return [
        index[start : start + BUNDLE_PATH_SIZE]
        .split(b"\0", 1)[0]
        .decode("utf-8", errors="replace")
        .lower()
        for start in range(0, index_size, BUNDLE_ENTRY_SIZE)
    ]
