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
    "ltx-1": "ltx-video.sh",
    "ltx1": "ltx-video.sh",
    "ltx-2": "ltx-video.sh",
    "ltx2": "ltx-video.sh",
    "audioldm": "audioldm.sh",
    "bark": "suno-bark.sh",
    "suno-bark": "suno-bark.sh",
}

_VIDEO_MODEL_FLAGS: dict[str, list[str]] = {
    "ltx-2": ["--ltx2"],
    "ltx2": ["--ltx2"],
    "ltx-1": ["--ltx1"],
    "ltx1": ["--ltx1"],
}


def _script_path(model: str) -> Path | None:
    script_name = _SCRIPT_NAMES.get(model)
    if not script_name:
        return None
    path = SCRIPTS_DIR / script_name
    return path if path.is_file() else None


def cmd_ai(kind: str, model: str | None, args: list[str]) -> int:
    extra: list[str] = []
    chosen = model or _DEFAULT_MODEL[kind]
    if kind == "video":
        extra = _VIDEO_MODEL_FLAGS.get(chosen, [])
        if chosen in _VIDEO_MODEL_FLAGS or chosen in ("ltx-video", "ltx-2", "ltx2", "ltx-1", "ltx1"):
            chosen = "ltx-video"
    script = _script_path(chosen)
    if script is None:
        known = ", ".join(sorted(set(_SCRIPT_NAMES)))
        print(f"Unknown model {model or chosen!r} for {kind}. Known: {known}", file=sys.stderr)
        return 1
    env = os.environ.copy()
    env["N0B_BIN"] = str(BIN_ROOT)
    rc = subprocess.run(["bash", str(script), *extra, *args], env=env).returncode
    return rc if rc is not None else 1
