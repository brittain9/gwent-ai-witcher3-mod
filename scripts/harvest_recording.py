#!/usr/bin/env python3
"""Reassemble flushed W3 Gwent recorder chunks from the local user settings file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from w3gwent.recording import (
        RecordingError,
        read_settings_section,
        settings_recordings,
    )
except ModuleNotFoundError:
    from recording import (
        RecordingError,
        read_settings_section,
        settings_recordings,
    )


DEFAULT_OUTPUT = ROOT / ".runtime/recordings"


def harvest(
    settings_file: Path, output_dir: Path, match_id: str | None = None
) -> list[Path]:
    recordings = settings_recordings(read_settings_section(settings_file))
    if match_id is None:
        selected = {
            name: recording
            for name, recording in recordings.items()
            if recording["status"] == "final"
        }
    else:
        recording = recordings.get(match_id)
        if recording is None:
            raise RecordingError(f"no flushed recording named {match_id}")
        if recording["status"] != "final":
            raise RecordingError(
                f"{match_id}: recording is incomplete ({recording['status']})"
            )
        selected = {match_id: recording}
    if not selected:
        raise RecordingError("recorder settings contain no completed matches")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for identifier, recording in selected.items():
        target = output_dir / f"{identifier}.jsonl"
        target.write_text(recording["jsonl"], encoding="utf-8")
        outputs.append(target)
        print(
            f"{identifier}: {recording['status']}, {recording['chunk_count']} chunks -> {target}"
        )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "settings_file", type=Path, help="dx12user.settings or user.settings"
    )
    parser.add_argument("--match", help="harvest only one mNNNNNN recording")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        harvest(args.settings_file, args.output_dir, args.match)
    except RecordingError as exc:
        print(f"harvest failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
