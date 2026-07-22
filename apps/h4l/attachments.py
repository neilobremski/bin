"""Parse a8s wake `ATTACHED FILE:` lines from an inbound hall message."""
from __future__ import annotations

import sys
from pathlib import Path

ATTACHED_FILE_PREFIX = "ATTACHED FILE: "


def split_attached_files(text: str) -> tuple[str, list[Path]]:
    """Strip trailing `ATTACHED FILE: <path>` lines; return body + existing paths.

    Missing paths are skipped with a stderr warning (wake still proceeds).
    """
    lines = text.splitlines()
    files: list[Path] = []
    while lines and lines[-1].startswith(ATTACHED_FILE_PREFIX):
        raw = lines.pop()[len(ATTACHED_FILE_PREFIX) :].strip()
        if not raw:
            continue
        path = Path(raw)
        if not path.is_file():
            print(
                f"h4l: skipping missing attachment {raw!r}",
                file=sys.stderr,
            )
            continue
        files.insert(0, path)
    body = "\n".join(lines).rstrip()
    return body, files
