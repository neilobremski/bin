"""Video utilities."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


def cmd_last_frame(video: str, output: str | None) -> int:
    import shutil

    video_path = Path(video)
    if not video_path.is_file():
        print(f"Error: Video file not found: {video}", file=sys.stderr)
        return 1
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found on PATH", file=sys.stderr)
        return 1
    out = output or f"last-frame-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.png"
    print(f"Extracting last frame from: {video}")
    rc = subprocess.run(
        [
            "ffmpeg",
            "-sseof",
            "-1",
            "-i",
            str(video_path),
            "-update",
            "1",
            "-q:v",
            "1",
            "-frames:v",
            "1",
            out,
        ]
    ).returncode
    if rc == 0:
        print(f"Last frame saved to: {out}")
    return rc if rc is not None else 1
