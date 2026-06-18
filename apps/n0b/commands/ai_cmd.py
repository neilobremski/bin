"""AI generation wrappers (image, video, audio)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from paths import BIN_ROOT, SCRIPTS_DIR

_DEFAULT_MODEL = {
    "image": "z-image",
    "video": "ltx-video",
    "audio": "audioldm",
}

_SCRIPT_NAMES = {
    "z-image": "z-image.sh",
    "ltx-video": "ltx-video.sh",
    "audioldm": "audioldm.sh",
    "bark": "suno-bark.sh",
    "suno-bark": "suno-bark.sh",
}


def _script_path(model: str) -> Path | None:
    script_name = _SCRIPT_NAMES.get(model)
    if not script_name:
        return None
    path = SCRIPTS_DIR / script_name
    return path if path.is_file() else None


def cmd_ai(kind: str, model: str | None, args: list[str]) -> int:
    chosen = model or _DEFAULT_MODEL[kind]
    script = _script_path(chosen)
    if script is None:
        known = ", ".join(sorted(_SCRIPT_NAMES))
        print(f"Unknown model {chosen!r} for {kind}. Known: {known}", file=sys.stderr)
        return 1
    env = os.environ.copy()
    env["N0B_BIN"] = str(BIN_ROOT)
    rc = subprocess.run(["bash", str(script), *args], env=env).returncode
    return rc if rc is not None else 1
