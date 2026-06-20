"""Resolve secrets from environment or ~/lib files."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def cmd_get(name: str) -> int:
    val = os.environ.get(name, "")
    if val:
        print(val, end="")
        return 0
    file_name = name.lower().replace("_", "-") + ".txt"
    path = Path.home() / "lib" / file_name
    if path.is_file():
        print(path.read_text().replace("\n", ""), end="")
        return 0
    print(f"error: {name} not found (env or {path})", file=sys.stderr)
    return 1
