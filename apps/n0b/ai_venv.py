"""Shared Python venv for n0b ai — re-exports repo-wide lib/venv_util."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _REPO_ROOT / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from venv_util import (  # noqa: E402
    AI_VENV,
    BIN_VENV,
    ensure_audio,
    ensure_image,
    ensure_kokoro,
    ensure_whisper,
    install_all,
    uninstall,
)

__all__ = [
    "AI_VENV",
    "BIN_VENV",
    "ensure_audio",
    "ensure_image",
    "ensure_kokoro",
    "ensure_whisper",
    "install_all",
    "uninstall",
]
