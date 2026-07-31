"""n0b ai transcribe — local Whisper, optional fancy video via Ollama vision."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ai_venv import ensure_whisper
from commands.ai_common import (
    parse_cli_pairs,
    read_hints,
    read_pair_file,
    save_hints,
    save_pair_file,
    split_cli_hints,
)
from commands.ai_ollama import (
    OllamaError,
    chat_with_images,
    ensure_vision_model,
    resolve_vision_model,
)

HINTS_FILE = Path.home() / ".config" / "n0b" / "transcribe-hints.txt"
REPLACEMENTS_FILE = Path.home() / ".config" / "n0b" / "transcribe-replacements.txt"

MAX_FRAMES = 50
MAX_FPS = 1.0
FRAME_SCALE = 512

_FANCY_SYSTEM = "You are my very helpful videographer analyst"

_FANCY_PROMPT = """\
I am analyzing a video via this sequence of images extracted from the frames at {fps} FPS as well as the audio transcript (which is below). I want to combine both of these to create a fancy transcription of the video.

First give a quick synopsis of what the entire video seems to be about in a short paragraph.

Next enumerate each distinct scene in order and with timing. Describe each in detail considering both the imagery and probable audio involved by matching times to frames. Figure out if any existing speech or sound effects are from a narrator, character, person, actor, or object in the environment and associate to them in the writing as dialog if appropriate (e.g. so-and-so says). I want to match audio visual reconstruct a rich narrative for each scene.

And finally at the end I'd like a summary of the entire video given all of the scene text already created in a short conclusive paragraph.

{speech}
"""

_WHISPER_SNIPPET = """\
import contextlib
import io
import sys
import whisper

audio, model_name, language, prompt = sys.argv[1:5]
print(f"loading model {model_name}...", file=sys.stderr)
model = whisper.load_model(model_name)
print(f"transcribing (language: {language or 'auto-detect'})...", file=sys.stderr)
# whisper prints "Detected language: ..." to stdout even with verbose=False
with contextlib.redirect_stdout(io.StringIO()):
    result = model.transcribe(
        audio,
        language=language or None,
        initial_prompt=prompt or None,
        fp16=False,
        verbose=False,
    )
print(result["text"].strip())
"""

_WHISPER_TIMED_SNIPPET = """\
import contextlib
import io
import json
import sys
import whisper

audio, model_name, language, prompt = sys.argv[1:5]
print(f"loading model {model_name}...", file=sys.stderr)
model = whisper.load_model(model_name)
print(f"transcribing (language: {language or 'auto-detect'})...", file=sys.stderr)
# whisper prints "Detected language: ..." to stdout even with verbose=False
with contextlib.redirect_stdout(io.StringIO()):
    result = model.transcribe(
        audio,
        language=language or None,
        initial_prompt=prompt or None,
        fp16=False,
        verbose=False,
    )
out = {
    "language": result.get("language") or "",
    "text": (result.get("text") or "").strip(),
    "segments": [
        {
            "start": float(seg.get("start") or 0),
            "end": float(seg.get("end") or 0),
            "text": (seg.get("text") or "").strip(),
        }
        for seg in (result.get("segments") or [])
    ],
}
print(json.dumps(out))
"""


def parse_whisper_timed_stdout(stdout: str) -> dict:
    """Parse timed Whisper JSON; tolerate stray non-JSON lines on stdout."""
    text = stdout.strip()
    if not text:
        raise ValueError("empty Whisper timed output")
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("Whisper timed output was not JSON")


def read_replacements(replacements_file: Path) -> list[tuple[str, str]]:
    return read_pair_file(replacements_file, "n0b ai transcribe")


def parse_cli_replacements(cli_replaces: list[str]) -> list[tuple[str, str]]:
    return parse_cli_pairs(cli_replaces, "n0b ai transcribe")


def apply_replacements(
    text: str, pairs: list[tuple[str, str]]
) -> tuple[str, list[str]]:
    applied: list[str] = []
    for pattern, correction in pairs:
        def annotate(m: re.Match[str]) -> str:
            return f"{m.group(0)} (possible transcribe error, might be '{correction}')"

        try:
            text, count = re.subn(pattern, annotate, text)
        except re.error as exc:
            print(
                f"n0b ai transcribe: bad replacement regex {pattern!r}: {exc}",
                file=sys.stderr,
            )
            continue
        if count:
            applied.append(f"{pattern} => {correction} (x{count})")
    return text, applied


def save_replacements(cli_replaces: list[str], replacements_file: Path) -> int:
    return save_pair_file(cli_replaces, replacements_file, "n0b ai transcribe")


def has_video_stream(path: Path) -> bool:
    if shutil.which("ffprobe") is None:
        return False
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return any(line.strip() == "video" for line in proc.stdout.splitlines())


def resolve_flavor(flavor: str, path: Path) -> str:
    if flavor in ("plain", "fancy"):
        return flavor
    return "fancy" if has_video_stream(path) else "plain"


def probe_duration_seconds(path: Path) -> float | None:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    raw = proc.stdout.strip()
    if not raw:
        return None
    try:
        duration = float(raw)
    except ValueError:
        return None
    if duration <= 0:
        return None
    return duration


def calc_fps(path: Path, max_frames: int = MAX_FRAMES, max_fps: float = MAX_FPS) -> float:
    duration = probe_duration_seconds(path)
    if duration is None:
        print(
            f"n0b ai transcribe: no duration; using max fps {max_fps}",
            file=sys.stderr,
        )
        return max_fps
    fps = max_frames / duration
    if fps > max_fps:
        return max_fps
    return round(fps, 2)


def extract_frames(
    path: Path,
    frames_dir: Path,
    fps: float,
    scale: int = FRAME_SCALE,
) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "%04d.png"
    print(f"extracting frames at {fps} FPS...", file=sys.stderr)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-vf",
            f"fps={fps},scale='min({scale},iw):-2'",
            str(pattern),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(detail or "ffmpeg frame extract failed")
    frames = sorted(frames_dir.glob("*.png"))
    if not frames:
        raise RuntimeError("no frames extracted")
    print(f"extracted {len(frames)} frame(s)", file=sys.stderr)
    return frames


def format_timed_speech(payload: dict) -> str:
    language = payload.get("language") or ""
    text = (payload.get("text") or "").strip()
    lines = [
        f"Language: {language}" if language else "Language: unknown",
        f"Full Speech Transcript: {text}",
        "",
    ]
    for seg in payload.get("segments") or []:
        start = round(float(seg.get("start") or 0), 2)
        end = round(float(seg.get("end") or 0), 2)
        seg_text = (seg.get("text") or "").strip()
        lines.append(f"[Speech Transcript from {start} to {end} seconds]")
        lines.append(seg_text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _run_whisper(
    python: Path,
    path: Path,
    model: str,
    language: str | None,
    prompt: str,
    *,
    timed: bool,
) -> tuple[int, str]:
    snippet = _WHISPER_TIMED_SNIPPET if timed else _WHISPER_SNIPPET
    proc = subprocess.run(
        [str(python), "-c", snippet, str(path), model, language or "", prompt],
        stdout=subprocess.PIPE,
        text=True,
    )
    return proc.returncode, proc.stdout


def _plain_transcribe(
    path: Path,
    python: Path,
    model: str,
    language: str | None,
    prompt: str,
    pairs: list[tuple[str, str]],
) -> int:
    rc, stdout = _run_whisper(python, path, model, language, prompt, timed=False)
    if rc != 0:
        return rc
    text, applied = apply_replacements(stdout.strip(), pairs)
    if pairs:
        note = "; ".join(applied) if applied else "none matched"
        print(f"replacements applied: {note}", file=sys.stderr)
    print(text)
    return 0


def _fancy_transcribe(
    path: Path,
    python: Path,
    model: str,
    language: str | None,
    prompt: str,
    pairs: list[tuple[str, str]],
    vision_model: str,
) -> int:
    if shutil.which("ffprobe") is None:
        print(
            "n0b ai transcribe: ffprobe not found (try: brew install ffmpeg)",
            file=sys.stderr,
        )
        return 1
    if not has_video_stream(path):
        print(
            "n0b ai transcribe: --flavor fancy requires a video stream "
            "(use --flavor plain or auto)",
            file=sys.stderr,
        )
        return 2

    try:
        ensure_vision_model(vision_model)
    except OllamaError as exc:
        print(f"n0b ai transcribe: {exc}", file=sys.stderr)
        return 1

    fps = calc_fps(path)
    rc, stdout = _run_whisper(python, path, model, language, prompt, timed=True)
    if rc != 0:
        return rc
    try:
        payload = parse_whisper_timed_stdout(stdout)
    except ValueError as exc:
        print(f"n0b ai transcribe: {exc}", file=sys.stderr)
        return 1

    speech = format_timed_speech(payload)
    speech, applied = apply_replacements(speech, pairs)
    if pairs:
        note = "; ".join(applied) if applied else "none matched"
        print(f"replacements applied: {note}", file=sys.stderr)
    if not speech.strip():
        speech = "No audio transcript available! Use images only"

    user_prompt = _FANCY_PROMPT.format(fps=fps, speech=speech)
    with tempfile.TemporaryDirectory(prefix="n0b-transcribe-") as tmp:
        frames_dir = Path(tmp) / "frames"
        try:
            frames = extract_frames(path, frames_dir, fps)
        except RuntimeError as exc:
            print(f"n0b ai transcribe: {exc}", file=sys.stderr)
            return 1
        print(
            f"vision model: {vision_model} ({len(frames)} frame(s))...",
            file=sys.stderr,
        )
        try:
            narrative = chat_with_images(
                vision_model,
                user_prompt,
                frames,
                system=_FANCY_SYSTEM,
            )
        except OllamaError as exc:
            print(f"n0b ai transcribe: {exc}", file=sys.stderr)
            return 1
    print(narrative)
    return 0


def cmd_transcribe(
    audio: str | None,
    hints: list[str],
    language: str | None,
    model: str,
    save: bool = False,
    replaces: list[str] | None = None,
    flavor: str = "auto",
    vision_model: str | None = None,
) -> int:
    replaces = replaces or []
    if save:
        saved = False
        if split_cli_hints(hints):
            save_hints(hints, HINTS_FILE)
            saved = True
        if parse_cli_replacements(replaces):
            save_replacements(replaces, REPLACEMENTS_FILE)
            saved = True
        if not saved:
            print(
                "n0b ai transcribe: --save needs at least one --hint or --replace",
                file=sys.stderr,
            )
            return 2
        if audio is None:
            return 0
    if audio is None:
        print("n0b ai transcribe: audio file required (or --save)", file=sys.stderr)
        return 2
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

    resolved = resolve_flavor(flavor, path)
    print(f"flavor: {resolved} (from {flavor})", file=sys.stderr)

    file_hints = read_hints(HINTS_FILE)
    cli_hints = split_cli_hints(hints)
    prompt = ", ".join(file_hints + cli_hints)
    if prompt:
        print(
            f"hints: {prompt}\n"
            f"  ({len(file_hints)} from {HINTS_FILE}, {len(cli_hints)} from --hint)",
            file=sys.stderr,
        )
    else:
        print(
            f"hints: none (create {HINTS_FILE}, or pass --hint, add --save to keep)",
            file=sys.stderr,
        )
    pairs = read_replacements(REPLACEMENTS_FILE) + parse_cli_replacements(replaces)
    if pairs:
        print(f"replacements: {len(pairs)} pattern(s) loaded", file=sys.stderr)
    try:
        python = ensure_whisper()
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai transcribe: Whisper setup failed: {exc}", file=sys.stderr)
        return 1

    if resolved == "plain":
        return _plain_transcribe(path, python, model, language, prompt, pairs)

    vision = resolve_vision_model(vision_model)
    return _fancy_transcribe(
        path, python, model, language, prompt, pairs, vision
    )
