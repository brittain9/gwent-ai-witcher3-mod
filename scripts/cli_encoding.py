"""Use UTF-8 for player-tool consoles and captured Python/Java output."""

from __future__ import annotations

import sys
from pathlib import Path

# Set both the default charset and the newer JDK stream charsets.
JAVA_UTF8_OPTIONS = (
    "-Dfile.encoding=UTF-8",
    "-Dstdout.encoding=UTF-8",
    "-Dstderr.encoding=UTF-8",
)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")


def python_command(script: Path | str, *arguments: str) -> list[str]:
    # -X also applies to embedded Python, whose isolated _pth ignores env vars.
    return [sys.executable, "-X", "utf8", str(script), *arguments]


def decode_output(output: bytes) -> str:
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"Child output is not UTF-8; original bytes: {output!r}"
        ) from error
