"""n0b ai speak — macOS say and Kokoro TTS."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ai_venv import ensure_kokoro
from commands.ai_common import parse_cli_pairs, read_pair_file, save_pair_file
from commands.speak_markdown import (
    DEFAULT_PAUSE_MAJOR,
    DEFAULT_PAUSE_MINOR,
    DEFAULT_PAUSE_PARA,
    PauseConfig,
    looks_like_markdown,
    map_span_text,
    parse_markdown_blocks,
    render_kokoro_pieces,
    render_say_text,
)

SPEAK_REPLACEMENTS_FILE = Path.home() / ".config" / "n0b" / "speak-replacements.txt"
SPEAK_PRONUNCIATIONS_FILE = Path.home() / ".config" / "n0b" / "speak-pronunciations.txt"
SPEAK_VOICE_FILE = Path.home() / ".config" / "n0b" / "speak-voice.txt"
DEFAULT_SPEAK_VOICE = "af_heart"

_KOKORO_SNIPPET = """\
import json
import sys

import numpy as np
import soundfile as sf
from kokoro import KPipeline

manifest_path, out_path, voice = sys.argv[1:4]
pieces = json.loads(open(manifest_path, encoding="utf-8").read())
pipeline = KPipeline(lang_code=voice[0])
chunks = []
sr = 24000
for i, piece in enumerate(pieces):
    text = piece.get("text") or ""
    speed = float(piece.get("speed", 1.0))
    silence_after = float(piece.get("silence_after", 0.0))
    if text.strip():
        for j, result in enumerate(pipeline(text, voice=voice, speed=speed)):
            audio = result[2]
            chunks.append(audio.numpy() if hasattr(audio, "numpy") else audio)
            print(f"  piece {i + 1}.{j + 1}", file=sys.stderr)
    if silence_after > 0:
        chunks.append(np.zeros(int(sr * silence_after), dtype=np.float32))
        print(f"  silence {silence_after:.2f}s", file=sys.stderr)
if not chunks:
    print("no audio produced", file=sys.stderr)
    sys.exit(1)
sf.write(out_path, np.concatenate(chunks), sr)
"""

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_NOISE_RE = re.compile(r"[*_`#>|]+")
_MISAKI_PHONEME_RE = re.compile(r"\[([^\]]+)\]\(/[^/]+/\)")
_MISAKI_KEEP_RE = re.compile(r"\[[^\]]+\]\(/[^/]+/\)")
_KOKORO_VOICE_RE = re.compile(r"^[abdefhpijzm][fm]_")


def _strip_md_links_keep_misaki(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        if _MISAKI_KEEP_RE.fullmatch(m.group(0)):
            return m.group(0)
        return m.group(1)

    return _MD_LINK_RE.sub(repl, text)


def speakable(markdown: str) -> str:
    out: list[str] = []
    fenced = False
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("```"):
            fenced = not fenced
            continue
        if fenced or s.startswith("|"):
            continue
        line = _strip_md_links_keep_misaki(line)
        line = _MD_NOISE_RE.sub("", line)
        out.append(line)
    return "\n".join(out)


def plain_for_say(text: str) -> str:
    return _MISAKI_PHONEME_RE.sub(r"\1", text)


def is_kokoro_voice(voice: str) -> bool:
    return any(_KOKORO_VOICE_RE.match(part.strip()) for part in voice.split(","))


def resolve_speak_engine(cli_engine: str | None) -> str:
    if cli_engine and cli_engine != "auto":
        return cli_engine
    if shutil.which("say"):
        return "say"
    return "kokoro"


def say_rate(speed: float) -> int:
    return max(50, min(500, int(175 * speed)))


def play_audio(path: Path) -> int:
    for player, extra in (
        ("afplay", []),
        ("aplay", []),
        ("ffplay", ["-nodisp", "-autoexit"]),
    ):
        if shutil.which(player):
            rc = subprocess.run([player, *extra, str(path)]).returncode
            return rc if rc is not None else 0
    print("n0b ai speak: no audio player found (afplay, aplay, ffplay)", file=sys.stderr)
    return 1


def read_sticky_voice(voice_file: Path | None = None) -> str | None:
    path = SPEAK_VOICE_FILE if voice_file is None else voice_file
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def save_sticky_voice(voice: str, voice_file: Path | None = None) -> int:
    path = SPEAK_VOICE_FILE if voice_file is None else voice_file
    voice = voice.strip()
    if not voice:
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{voice}\n")
    print(f"saved default voice to {path}", file=sys.stderr)
    return 0


def resolve_speak_voice(cli_voice: str | None, engine: str) -> tuple[str | None, str]:
    if cli_voice is not None:
        return cli_voice, "cli"
    sticky = read_sticky_voice()
    if sticky:
        return sticky, str(SPEAK_VOICE_FILE)
    if engine == "kokoro":
        return DEFAULT_SPEAK_VOICE, "built-in default"
    return None, "system default"


def apply_speak_replacements(
    text: str, pairs: list[tuple[str, str]]
) -> tuple[str, list[str]]:
    applied: list[str] = []
    for pattern, spoken in pairs:
        try:
            text, count = re.subn(pattern, spoken, text)
        except re.error as exc:
            print(
                f"n0b ai speak: bad replacement regex {pattern!r}: {exc}",
                file=sys.stderr,
            )
            continue
        if count:
            applied.append(f"{pattern} => {spoken} (x{count})")
    return text, applied


def apply_pronunciations(
    text: str, pairs: list[tuple[str, str]]
) -> tuple[str, list[str]]:
    applied: list[str] = []
    for pattern, ipa in pairs:
        def wrap(m: re.Match[str]) -> str:
            word = m.group(0)
            return f"[{word}](/{ipa}/)"

        try:
            text, count = re.subn(pattern, wrap, text)
        except re.error as exc:
            print(
                f"n0b ai speak: bad pronunciation regex {pattern!r}: {exc}",
                file=sys.stderr,
            )
            continue
        if count:
            applied.append(f"{pattern} => /{ipa}/ (x{count})")
    return text, applied


def load_speak_text(parts: list[str]) -> str:
    if not parts:
        return sys.stdin.read()
    if parts[0] == "--":
        parts = parts[1:]
    if not parts:
        return sys.stdin.read()
    if len(parts) == 1 and parts[0] == "-":
        return sys.stdin.read()
    if len(parts) == 1:
        path = Path(parts[0]).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return " ".join(parts)


def _speak_say(
    text: str,
    voice: str | None,
    speed: float,
    out: Path | None,
) -> int:
    if shutil.which("say") is None:
        print("n0b ai speak: say not found", file=sys.stderr)
        return 1
    text_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", encoding="utf-8", delete=False
        ) as f:
            f.write(text)
            text_file = Path(f.name)
        cmd = ["say"]
        if voice:
            if is_kokoro_voice(voice):
                print(
                    f"n0b ai speak: ignoring Kokoro voice {voice!r} for say engine",
                    file=sys.stderr,
                )
            else:
                cmd.extend(["-v", voice])
        if speed != 1.0:
            cmd.extend(["-r", str(say_rate(speed))])
        if out is None:
            cmd.extend(["-f", str(text_file)])
            proc = subprocess.run(cmd)
            return proc.returncode if proc.returncode is not None else 0
        out.parent.mkdir(parents=True, exist_ok=True)
        say_out = out
        convert_wav = out.suffix.lower() == ".wav"
        if convert_wav:
            if shutil.which("afconvert") is None:
                print(
                    "n0b ai speak: .wav output needs afconvert (macOS) — use .m4a or .aiff",
                    file=sys.stderr,
                )
                return 1
            say_out = out.with_suffix(".tmp.aiff")
        cmd.extend(["-o", str(say_out), "-f", str(text_file)])
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            return proc.returncode
        if convert_wav:
            conv = subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16", str(say_out), str(out)],
            )
            say_out.unlink(missing_ok=True)
            if conv.returncode != 0:
                return conv.returncode
        print(out)
        return 0
    finally:
        if text_file is not None:
            text_file.unlink(missing_ok=True)


def _speak_kokoro(
    pieces: list[dict],
    voice: str,
    out: Path | None,
) -> int:
    play = out is None
    convert = False
    if out is None:
        out_path = Path(tempfile.mktemp(suffix=".wav"))
    else:
        out_path = out
        convert = out_path.suffix.lower() in (".m4a", ".aac", ".mp4")
        if convert and shutil.which("afconvert") is None:
            print(
                "n0b ai speak: compressed output needs afconvert (macOS) — "
                "use a .wav path",
                file=sys.stderr,
            )
            return 1
        if not convert and out_path.suffix.lower() != ".wav":
            print(
                f"n0b ai speak: unsupported output format {out_path.suffix!r} "
                "(use .wav or .m4a)",
                file=sys.stderr,
            )
            return 2
    try:
        python = ensure_kokoro()
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai speak: Kokoro setup failed: {exc}", file=sys.stderr)
        return 1
    wav_path = out_path.with_suffix(".tmp.wav") if convert else out_path
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", encoding="utf-8", delete=False
    ) as f:
        json.dump(pieces, f)
        manifest_file = Path(f.name)
    try:
        proc = subprocess.run(
            [
                str(python),
                "-c",
                _KOKORO_SNIPPET,
                str(manifest_file),
                str(wav_path),
                voice,
            ],
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
        manifest_file.unlink(missing_ok=True)
    if play:
        try:
            return play_audio(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)
    print(out_path)
    return 0


def _apply_pairs_to_text(
    text: str,
    *,
    engine_name: str,
    replace_pairs: list[tuple[str, str]],
    pronounce_pairs: list[tuple[str, str]],
    file_replaces: list[tuple[str, str]],
    cli_replaces: list[tuple[str, str]],
    file_pronounces: list[tuple[str, str]],
    cli_pronounces: list[tuple[str, str]],
) -> str:
    if pronounce_pairs and engine_name == "kokoro":
        print(
            f"pronunciations: {len(pronounce_pairs)} pattern(s) loaded "
            f"({len(file_pronounces)} from {SPEAK_PRONUNCIATIONS_FILE}, "
            f"{len(cli_pronounces)} from --pronounce)",
            file=sys.stderr,
        )
        text, applied = apply_pronunciations(text, pronounce_pairs)
        note = "; ".join(applied) if applied else "none matched"
        print(f"pronunciations applied: {note}", file=sys.stderr)
    elif pronounce_pairs:
        print(
            "n0b ai speak: pronunciations ignored for say engine "
            "(use --engine kokoro)",
            file=sys.stderr,
        )
    if replace_pairs:
        print(
            f"replacements: {len(replace_pairs)} pattern(s) loaded "
            f"({len(file_replaces)} from {SPEAK_REPLACEMENTS_FILE}, "
            f"{len(cli_replaces)} from --replace)",
            file=sys.stderr,
        )
        text, applied = apply_speak_replacements(text, replace_pairs)
        note = "; ".join(applied) if applied else "none matched"
        print(f"replacements applied: {note}", file=sys.stderr)
    return text


def cmd_speak(
    source: list[str] | None,
    out: str | None,
    voice: str | None,
    speed: float,
    raw: bool = False,
    flat: bool = False,
    replaces: list[str] | None = None,
    pronounces: list[str] | None = None,
    save: bool = False,
    engine: str | None = None,
    pause_major: float = DEFAULT_PAUSE_MAJOR,
    pause_minor: float = DEFAULT_PAUSE_MINOR,
    pause_para: float = DEFAULT_PAUSE_PARA,
    emphasis: bool = True,
) -> int:
    replaces = replaces or []
    pronounces = pronounces or []
    if save:
        saved = False
        if parse_cli_pairs(replaces, "n0b ai speak"):
            save_pair_file(replaces, SPEAK_REPLACEMENTS_FILE, "n0b ai speak")
            saved = True
        if parse_cli_pairs(pronounces, "n0b ai speak"):
            save_pair_file(pronounces, SPEAK_PRONUNCIATIONS_FILE, "n0b ai speak")
            saved = True
        if voice is not None:
            save_sticky_voice(voice)
            saved = True
        if not saved:
            print(
                "n0b ai speak: --save needs --voice, --replace, and/or --pronounce",
                file=sys.stderr,
            )
            return 2
        if not source:
            return 0
    engine_name = resolve_speak_engine(engine)
    voice, voice_src = resolve_speak_voice(voice, engine_name)
    print(f"engine: {engine_name}", file=sys.stderr)
    print(f"voice: {voice or 'default'} ({voice_src})", file=sys.stderr)
    text = load_speak_text(source or [])
    file_replaces = read_pair_file(SPEAK_REPLACEMENTS_FILE, "n0b ai speak")
    cli_replaces = parse_cli_pairs(replaces, "n0b ai speak")
    replace_pairs = file_replaces + cli_replaces
    file_pronounces = read_pair_file(SPEAK_PRONUNCIATIONS_FILE, "n0b ai speak")
    cli_pronounces = parse_cli_pairs(pronounces, "n0b ai speak")
    pronounce_pairs = file_pronounces + cli_pronounces
    out_path = Path(out).expanduser() if out else None
    structured = (
        not raw
        and not flat
        and looks_like_markdown(text)
    )
    if structured:
        pauses = PauseConfig(pause_major, pause_minor, pause_para)
        blocks = parse_markdown_blocks(text, pauses)
        use_ipa = bool(pronounce_pairs) and engine_name == "kokoro"
        if pronounce_pairs and not use_ipa:
            print(
                "n0b ai speak: pronunciations ignored for say engine "
                "(use --engine kokoro)",
                file=sys.stderr,
            )
        if use_ipa:
            print(
                f"pronunciations: {len(pronounce_pairs)} pattern(s) loaded "
                f"({len(file_pronounces)} from {SPEAK_PRONUNCIATIONS_FILE}, "
                f"{len(cli_pronounces)} from --pronounce)",
                file=sys.stderr,
            )
        if replace_pairs:
            print(
                f"replacements: {len(replace_pairs)} pattern(s) loaded "
                f"({len(file_replaces)} from {SPEAK_REPLACEMENTS_FILE}, "
                f"{len(cli_replaces)} from --replace)",
                file=sys.stderr,
            )
        ipa_notes: list[str] = []
        repl_notes: list[str] = []

        def transform(span_text: str) -> str:
            out_text = span_text
            if use_ipa:
                out_text, applied = apply_pronunciations(out_text, pronounce_pairs)
                ipa_notes.extend(applied)
            if replace_pairs:
                out_text, applied = apply_speak_replacements(out_text, replace_pairs)
                repl_notes.extend(applied)
            return out_text

        blocks = map_span_text(blocks, transform)
        if use_ipa:
            note = "; ".join(ipa_notes) if ipa_notes else "none matched"
            print(f"pronunciations applied: {note}", file=sys.stderr)
        if replace_pairs:
            note = "; ".join(repl_notes) if repl_notes else "none matched"
            print(f"replacements applied: {note}", file=sys.stderr)
        if not blocks:
            print("n0b ai speak: nothing to say after cleanup", file=sys.stderr)
            return 2
        print(
            f"markdown: {len(blocks)} block(s); "
            f"pauses major={pauses.major}s minor={pauses.minor}s "
            f"para={pauses.para}s; emphasis={'on' if emphasis else 'off'}",
            file=sys.stderr,
        )
        if engine_name == "say":
            say_text = plain_for_say(render_say_text(blocks, emphasis=emphasis))
            return _speak_say(say_text, voice, speed, out_path)
        if voice is None:
            voice = DEFAULT_SPEAK_VOICE
        pieces = [
            {
                "text": p.text,
                "speed": p.speed,
                "silence_after": p.silence_after,
            }
            for p in render_kokoro_pieces(
                blocks, base_speed=speed, emphasis=emphasis
            )
            if p.text.strip() or p.silence_after > 0
        ]
        if not any(p["text"].strip() for p in pieces):
            print("n0b ai speak: nothing to say after cleanup", file=sys.stderr)
            return 2
        return _speak_kokoro(pieces, voice, out_path)

    if not raw:
        text = speakable(text)
    text = _apply_pairs_to_text(
        text,
        engine_name=engine_name,
        replace_pairs=replace_pairs,
        pronounce_pairs=pronounce_pairs,
        file_replaces=file_replaces,
        cli_replaces=cli_replaces,
        file_pronounces=file_pronounces,
        cli_pronounces=cli_pronounces,
    )
    if not text.strip():
        print("n0b ai speak: nothing to say after cleanup", file=sys.stderr)
        return 2
    if engine_name == "say":
        return _speak_say(plain_for_say(text), voice, speed, out_path)
    if voice is None:
        voice = DEFAULT_SPEAK_VOICE
    return _speak_kokoro(
        [{"text": text, "speed": speed, "silence_after": 0.0}],
        voice,
        out_path,
    )
