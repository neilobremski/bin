"""Shared paths for n0b."""
from __future__ import annotations

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BIN_ROOT = SCRIPT_DIR.parent.parent
SCRIPTS_DIR = SCRIPT_DIR / "scripts"
ENTRYPOINT = SCRIPT_DIR / "n0b.py"
