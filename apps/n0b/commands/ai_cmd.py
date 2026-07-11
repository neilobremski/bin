"""AI generation wrappers (image, video, audio), transcription, and deep research."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
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
REPLACEMENTS_FILE = Path.home() / ".config" / "n0b" / "transcribe-replacements.txt"

_WHISPER_SNIPPET = """\
import sys
import whisper

audio, model_name, language, prompt = sys.argv[1:5]
print(f"loading model {model_name}...", file=sys.stderr)
model = whisper.load_model(model_name)
print(f"transcribing (language: {language or 'auto-detect'})...", file=sys.stderr)
result = model.transcribe(
    audio,
    language=language or None,
    initial_prompt=prompt or None,
    fp16=False,
    verbose=False,
)
print(result["text"].strip())
"""


_KOKORO_SNIPPET = """\
import sys

import numpy as np
import soundfile as sf
from kokoro import KPipeline

text_path, out_path, voice, speed = sys.argv[1:5]
text = open(text_path, encoding="utf-8").read()
pipeline = KPipeline(lang_code=voice[0])
chunks = []
for i, result in enumerate(pipeline(text, voice=voice, speed=float(speed))):
    audio = result[2]
    chunks.append(audio.numpy() if hasattr(audio, "numpy") else audio)
    print(f"  segment {i + 1}", file=sys.stderr)
if not chunks:
    print("no audio produced", file=sys.stderr)
    sys.exit(1)
sf.write(out_path, np.concatenate(chunks), 24000)
"""

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_NOISE_RE = re.compile(r"[*_`#>|]+")


def speakable(markdown: str) -> str:
    """Reduce markdown to prose worth hearing: drop code fences and table
    rows, unwrap links, strip emphasis markers."""
    out: list[str] = []
    fenced = False
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("```"):
            fenced = not fenced
            continue
        if fenced or s.startswith("|"):
            continue
        line = _MD_LINK_RE.sub(r"\1", line)
        line = _MD_NOISE_RE.sub("", line)
        out.append(line)
    return "\n".join(out)


KOKORO_VENV = Path.home() / ".cache" / "n0b" / "kokoro-venv"


def _kokoro_base_python() -> str:
    # kokoro -> misaki[en] -> spacy, which trails new CPython releases;
    # prefer an interpreter old enough to have wheels.
    for name in ("python3.13", "python3.12", "python3.11"):
        if shutil.which(name):
            return name
    return sys.executable


def _kokoro_python() -> Path:
    python = KOKORO_VENV / "bin" / "python3"
    if python.is_file():
        probe = subprocess.run(
            [str(python), "-c", "import kokoro, soundfile"], capture_output=True
        )
        if probe.returncode == 0:
            return python
    else:
        print(f"Setting up Kokoro venv at {KOKORO_VENV} (one-time)...", file=sys.stderr)
        KOKORO_VENV.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [_kokoro_base_python(), "-m", "venv", str(KOKORO_VENV)],
            check=True, stdout=sys.stderr,
        )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        check=True, stdout=sys.stderr,
    )
    # spacy 4.x ships sdist-only for this platform and its build chain is
    # broken; the <4 pin keeps pip on the 3.8 wheels misaki actually needs.
    # phonemizer enables misaki's espeak fallback for names and other
    # out-of-dictionary words (the library itself comes from espeak-ng).
    subprocess.run(
        [str(python), "-m", "pip", "install", "spacy<4", "kokoro", "soundfile",
         "phonemizer"],
        check=True, stdout=sys.stderr,
    )
    return python


def cmd_speak(
    source: str | None,
    out: str | None,
    voice: str,
    speed: float,
    raw: bool = False,
) -> int:
    if source is None or source == "-":
        text = sys.stdin.read()
        stem = "speech"
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            print(f"n0b ai speak: no such file: {source}", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8")
        stem = path.stem
    if not raw:
        text = speakable(text)
    if not text.strip():
        print("n0b ai speak: nothing to say after cleanup", file=sys.stderr)
        return 2
    out_path = Path(out).expanduser() if out else Path(f"{stem}.wav")
    convert = out_path.suffix.lower() in (".m4a", ".aac", ".mp4")
    if convert and shutil.which("afconvert") is None:
        print(
            "n0b ai speak: compressed output needs afconvert (macOS) — "
            "use a .wav path",
            file=sys.stderr,
        )
        return 1
    if not convert and out_path.suffix.lower() != ".wav":
        print(f"n0b ai speak: unsupported output format {out_path.suffix!r} "
              "(use .wav or .m4a)", file=sys.stderr)
        return 2
    try:
        python = _kokoro_python()
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai speak: Kokoro setup failed: {exc}", file=sys.stderr)
        return 1
    wav_path = out_path.with_suffix(".tmp.wav") if convert else out_path
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(text)
        text_file = Path(f.name)
    try:
        proc = subprocess.run(
            [str(python), "-c", _KOKORO_SNIPPET, str(text_file), str(wav_path),
             voice, str(speed)],
        )
        if proc.returncode != 0:
            return proc.returncode
        if convert:
            conv = subprocess.run(
                ["afconvert", "-f", "m4af", "-d", "aac", str(wav_path),
                 str(out_path)],
            )
            wav_path.unlink(missing_ok=True)
            if conv.returncode != 0:
                return conv.returncode
    finally:
        text_file.unlink(missing_ok=True)
    print(out_path)
    return 0


def read_hints(hints_file: Path) -> list[str]:
    if not hints_file.is_file():
        return []
    hints: list[str] = []
    for line in hints_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            hints.append(line)
    return hints


def split_cli_hints(cli_hints: list[str]) -> list[str]:
    return [p.strip() for h in cli_hints for p in h.split(",") if p.strip()]


def merged_hints(cli_hints: list[str], hints_file: Path) -> str:
    return ", ".join(read_hints(hints_file) + split_cli_hints(cli_hints))


def parse_replacement(line: str) -> tuple[str, str] | None:
    if "=>" not in line:
        return None
    pattern, correction = line.split("=>", 1)
    pattern, correction = pattern.strip(), correction.strip()
    if not pattern or not correction:
        return None
    return pattern, correction


def read_replacements(replacements_file: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not replacements_file.is_file():
        return pairs
    for line in replacements_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pair = parse_replacement(line)
        if pair is None:
            print(
                f"n0b ai transcribe: skipping bad replacement line "
                f"(want 'wrong => right'): {line!r}",
                file=sys.stderr,
            )
            continue
        pairs.append(pair)
    return pairs


def parse_cli_replacements(cli_replaces: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw in cli_replaces:
        pair = parse_replacement(raw)
        if pair is None:
            print(
                f"n0b ai transcribe: bad --replace value "
                f"(want 'wrong => right'): {raw!r}",
                file=sys.stderr,
            )
            continue
        pairs.append(pair)
    return pairs


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
    new = parse_cli_replacements(cli_replaces)
    if not new:
        return 2
    known = {pattern for pattern, _ in read_replacements(replacements_file)}
    added = [(p, c) for p, c in new if p not in known]
    if added:
        replacements_file.parent.mkdir(parents=True, exist_ok=True)
        lead = ""
        if replacements_file.is_file():
            text = replacements_file.read_text()
            if text and not text.endswith("\n"):
                lead = "\n"
        with replacements_file.open("a") as f:
            f.write(lead + "".join(f"{p} => {c}\n" for p, c in added))
    print(
        f"saved {len(added)} replacement(s) to {replacements_file}"
        + (f" ({len(new) - len(added)} already there)" if len(added) < len(new) else ""),
        file=sys.stderr,
    )
    return 0


def save_hints(cli_hints: list[str], hints_file: Path) -> int:
    new = split_cli_hints(cli_hints)
    if not new:
        return 2
    existing = read_hints(hints_file)
    known = {h.lower() for h in existing}
    added = []
    for hint in new:
        if hint.lower() not in known:
            known.add(hint.lower())
            added.append(hint)
    if added:
        hints_file.parent.mkdir(parents=True, exist_ok=True)
        lead = ""
        if hints_file.is_file():
            text = hints_file.read_text()
            if text and not text.endswith("\n"):
                lead = "\n"
        with hints_file.open("a") as f:
            f.write(lead + "".join(f"{h}\n" for h in added))
    print(
        f"saved {len(added)} hint(s) to {hints_file}"
        + (f" ({len(new) - len(added)} already there)" if len(added) < len(new) else ""),
        file=sys.stderr,
    )
    return 0


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
    audio: str | None,
    hints: list[str],
    language: str | None,
    model: str,
    save: bool = False,
    replaces: list[str] | None = None,
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
        python = _whisper_python()
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai transcribe: Whisper setup failed: {exc}", file=sys.stderr)
        return 1
    proc = subprocess.run(
        [str(python), "-c", _WHISPER_SNIPPET, str(path), model, language or "", prompt],
        stdout=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        return proc.returncode
    text, applied = apply_replacements(proc.stdout.strip(), pairs)
    if pairs:
        note = "; ".join(applied) if applied else "none matched"
        print(f"replacements applied: {note}", file=sys.stderr)
    print(text)
    return 0


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
