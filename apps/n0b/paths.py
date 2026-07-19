"""Shared paths for n0b."""
from __future__ import annotations

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BIN_ROOT = SCRIPT_DIR.parent.parent
LIB_DIR = BIN_ROOT / "lib"
VENV_DIR = BIN_ROOT / ".venv"
REQUIREMENTS_DIR = BIN_ROOT / "requirements"
SCRIPTS_DIR = SCRIPT_DIR / "scripts"  # legacy; may be empty
ENTRYPOINT = SCRIPT_DIR / "n0b.py"
