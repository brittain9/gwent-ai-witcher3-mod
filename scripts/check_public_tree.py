"""Check the Git-visible source set before review or publication."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PARTS = {
    "AGENTS.md",
    "CLAUDE.md",
    ".private",
    ".local",
    ".runtime",
    ".venv",
    "engine",
    "engine-py",
    "data",
    "__pycache__",
}
BLOCKED_SUFFIXES = {
    ".pt",
    ".bin",
    ".swf",
    ".redswf",
    ".bundle",
    ".store",
    ".jsonl",
    ".pyc",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".zip",
}
PRIVATE_MARKERS = (
    "/" + "home/",
    "/" + "Users/",
    "gwent-" + "2018",
    "docs/" + "ai/",
    "docs/" + "sessions/",
    "Co-" + "Authored-By",
)


def check_paths(root: Path, paths: list[str]) -> list[str]:
    errors = []
    for name in sorted(set(paths)):
        path = Path(name)
        source = root / path
        if (
            path.is_absolute()
            or ".." in path.parts
            or set(path.parts) & BLOCKED_PARTS
            or path.suffix in BLOCKED_SUFFIXES
        ):
            errors.append(f"excluded material: {name}")
            continue
        if source.is_symlink():
            errors.append(f"symlink: {name}")
            continue
        try:
            content = source.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            errors.append(f"not readable source text: {name}")
            continue
        if any(marker in content for marker in PRIVATE_MARKERS):
            errors.append(f"local reference: {name}")
    return errors


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    paths = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    errors = check_paths(ROOT, paths)
    for error in errors:
        print(error)
    print(f"Checked {len(set(paths))} public source files: {len(errors)} problems")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
