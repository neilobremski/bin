"""AI generation wrappers (image, video, audio), transcription, and deep research."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from paths import BIN_ROOT, SCRIPTS_DIR
from research import run_research

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


def cmd_research(args: list[str]) -> int:
    if not args:
        print("Usage: n0b ai research <prompt...>", file=sys.stderr)
        return 2
    return run_research(args)


WHISPER_VENV = Path.home() / ".cache" / "n0b" / "whisper-venv"
HINTS_FILE = Path.home() / ".config" / "n0b" / "transcribe-hints.txt"

_WHISPER_SNIPPET = """\
import sys
import whisper

audio, model_name, language, prompt = sys.argv[1:5]
model = whisper.load_model(model_name)
result = model.transcribe(
    audio,
    language=language or None,
    initial_prompt=prompt or None,
    fp16=False,
)
print(result["text"].strip())
"""


def merged_hints(cli_hints: list[str], hints_file: Path) -> str:
    hints: list[str] = []
    if hints_file.is_file():
        for line in hints_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                hints.append(line)
    hints.extend(h.strip() for h in cli_hints if h.strip())
    return ", ".join(hints)


def _whisper_python() -> Path:
    python = WHISPER_VENV / "bin" / "python3"
    if python.is_file():
        return python
    print(f"Setting up Whisper venv at {WHISPER_VENV} (one-time)...", file=sys.stderr)
    WHISPER_VENV.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(WHISPER_VENV)],
        check=True, stdout=sys.stderr,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        check=True, stdout=sys.stderr,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "openai-whisper"],
        check=True, stdout=sys.stderr,
    )
    return python


def cmd_transcribe(
    audio: str, hints: list[str], language: str | None, model: str
) -> int:
    path = Path(audio).expanduser()
    if not path.is_file():
        print(f"n0b ai transcribe: no such file: {audio}", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None:
        print(
            "n0b ai transcribe: ffmpeg not found (try: brew install ffmpeg)",
            file=sys.stderr,
        )
        return 1
    try:
        python = _whisper_python()
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai transcribe: Whisper setup failed: {exc}", file=sys.stderr)
        return 1
    proc = subprocess.run(
        [
            str(python), "-c", _WHISPER_SNIPPET,
            str(path), model, language or "", merged_hints(hints, HINTS_FILE),
        ]
    )
    return proc.returncode


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
