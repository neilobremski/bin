"""AI generation wrappers (image, video, audio), transcription, and deep research."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from ai_venv import AI_VENV, ensure_image, ensure_kokoro, ensure_whisper, uninstall as ai_venv_uninstall
from research import run_research


def resolve_image_ref(
    refs: list[str],
) -> tuple[str | None, str | None, str | None]:
    if not refs:
        return None, None, None
    path = Path(refs[0]).expanduser()
    if not path.is_file():
        return None, None, f"n0b ai image: no such reference file: {refs[0]}"
    note = None
    if len(refs) > 1:
        note = (
            f"n0b ai image: using first --ref only ({path}); "
            "Z-Image-Turbo supports one reference image"
        )
    return str(path), note, None


def build_image_argv(
    prompt: list[str],
    refs: list[str],
    strength: float,
    out: str | None,
) -> tuple[list[str], str | None]:
    rest = list(prompt)
    if rest[:1] == ["--"]:
        rest = rest[1:]
    ref, note, err = resolve_image_ref(refs)
    if err:
        raise ValueError(err)
    if refs and not 0.0 <= strength <= 1.0:
        raise ValueError("n0b ai image: --strength must be between 0.0 and 1.0")
    if strength != 0.6 and ref is None:
        raise ValueError("n0b ai image: --strength requires --ref")
    argv: list[str] = []
    if ref is not None:
        argv.extend(["--ref", ref, "--strength", str(strength)])
    if out:
        argv.extend(["-o", out])
    argv.extend(rest)
    return argv, note


ZIMAGE_MODEL = "Tongyi-MAI/Z-Image-Turbo"

_ZIMAGE_SNIPPET = """\
import sys
import warnings

import torch

warnings.filterwarnings("ignore")

prompt, out_path, ref_path, strength, width, height = sys.argv[1:7]
width, height = int(width), int(height)

if torch.cuda.is_available():
    device = "cuda"
elif torch.backends.mps.is_available():
    device = "mps"
else:
    device = "cpu"
dtype = torch.bfloat16

if ref_path:
    from diffusers import ZImageImg2ImgPipeline
    from PIL import Image

    print(f"img2img: ref={ref_path} strength={strength}", file=sys.stderr)
    pipe = ZImageImg2ImgPipeline.from_pretrained(%r, torch_dtype=dtype)
    pipe.to(device)
    init = Image.open(ref_path).convert("RGB").resize((width, height))
    image = pipe(
        prompt,
        image=init,
        strength=float(strength),
        height=height,
        width=width,
        num_inference_steps=8,
        guidance_scale=0.0,
    ).images[0]
else:
    from diffusers import ZImagePipeline

    print("loading Z-Image-Turbo...", file=sys.stderr)
    pipe = ZImagePipeline.from_pretrained(%r, torch_dtype=dtype)
    pipe.to(device)
    image = pipe(
        prompt,
        height=height,
        width=width,
        num_inference_steps=9,
        guidance_scale=0.0,
    ).images[0]

image.save(out_path)
print(out_path)
""" % (ZIMAGE_MODEL, ZIMAGE_MODEL)


def zimage_uninstall() -> int:
    return ai_venv_uninstall()


def cmd_image(
    model: str | None,
    prompt: list[str],
    refs: list[str],
    strength: float,
    out: str | None,
    width: int | None = None,
    height: int | None = None,
    aspect_16_9: bool = False,
    install: bool = False,
    uninstall: bool = False,
) -> int:
    if uninstall:
        return zimage_uninstall()
    if model and model != "z-image":
        print(
            f"n0b ai image: unknown model {model!r} (known: z-image)",
            file=sys.stderr,
        )
        return 1
    parts = list(prompt)
    if parts[:1] == ["--"]:
        parts = parts[1:]
    if install and not parts and not refs:
        try:
            ensure_image()
        except subprocess.CalledProcessError as exc:
            print(f"n0b ai image: setup failed: {exc}", file=sys.stderr)
            return 1
        print(f"ready: {AI_VENV}", file=sys.stderr)
        return 0
    if not parts:
        print("n0b ai image: prompt required", file=sys.stderr)
        return 2
    try:
        _, note = build_image_argv(parts, refs, strength, out)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        msg = str(exc)
        return 1 if "no such reference" in msg else 2
    if note:
        print(note, file=sys.stderr)
    ref_path, _, err = resolve_image_ref(refs)
    if err:
        print(err, file=sys.stderr)
        return 1
    prompt_text = " ".join(parts)
    if aspect_16_9:
        gen_width, gen_height = 1920, 1088
    else:
        gen_width, gen_height = width or 1024, height or 1024
    out_path = (
        Path(out).expanduser()
        if out
        else Path(f"z-image-{datetime.now():%Y-%m-%d-%H-%M-%S}.png")
    )
    try:
        python = ensure_image()
    except subprocess.CalledProcessError as exc:
        print(f"n0b ai image: setup failed: {exc}", file=sys.stderr)
        return 1
    proc = subprocess.run(
        [
            str(python),
            "-c",
            _ZIMAGE_SNIPPET,
            prompt_text,
            str(out_path),
            ref_path or "",
            str(strength),
            str(gen_width),
            str(gen_height),
        ],
    )
    return proc.returncode if proc.returncode is not None else 0


def cmd_research(args: list[str]) -> int:
    if not args:
        print("Usage: n0b ai research <prompt...>", file=sys.stderr)
        return 2
    return run_research(args)


HINTS_FILE = Path.home() / ".config" / "n0b" / "transcribe-hints.txt"
REPLACEMENTS_FILE = Path.home() / ".config" / "n0b" / "transcribe-replacements.txt"
SPEAK_REPLACEMENTS_FILE = Path.home() / ".config" / "n0b" / "speak-replacements.txt"
SPEAK_PRONUNCIATIONS_FILE = Path.home() / ".config" / "n0b" / "speak-pronunciations.txt"
SPEAK_VOICE_FILE = Path.home() / ".config" / "n0b" / "speak-voice.txt"
DEFAULT_SPEAK_VOICE = "af_heart"

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

_MD_URL_LINK_RE = re.compile(r"\[([^\]]+)\]\((?!/)[^)]*\)")
_MD_NOISE_RE = re.compile(r"[*_`#>|]+")
_MISAKI_PHONEME_RE = re.compile(r"\[([^\]]+)\]\(/[^/]+/\)")
_KOKORO_VOICE_RE = re.compile(r"^[abdefhpijzm][fm]_")


def speakable(markdown: str) -> str:
    """Reduce markdown to prose worth hearing: drop code fences and table
    rows, unwrap URL links, strip emphasis markers. Keeps misaki phoneme
    overrides like [word](/ipa/)."""
    out: list[str] = []
    fenced = False
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("```"):
            fenced = not fenced
            continue
        if fenced or s.startswith("|"):
            continue
        line = _MD_URL_LINK_RE.sub(r"\1", line)
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
    text: str,
    voice: str,
    speed: float,
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
    if play:
        try:
            return play_audio(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)
    print(out_path)
    return 0


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


def read_pair_file(
    pair_file: Path, label: str = "n0b"
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not pair_file.is_file():
        return pairs
    for line in pair_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pair = parse_replacement(line)
        if pair is None:
            print(
                f"{label}: skipping bad line (want 'left => right'): {line!r}",
                file=sys.stderr,
            )
            continue
        pairs.append(pair)
    return pairs


def parse_cli_pairs(cli_values: list[str], label: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw in cli_values:
        pair = parse_replacement(raw)
        if pair is None:
            print(
                f"{label}: bad value (want 'left => right'): {raw!r}",
                file=sys.stderr,
            )
            continue
        pairs.append(pair)
    return pairs


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


def save_pair_file(
    cli_values: list[str],
    pair_file: Path,
    label: str,
) -> int:
    new = parse_cli_pairs(cli_values, label)
    if not new:
        return 2
    known = {pattern for pattern, _ in read_pair_file(pair_file, label)}
    added = [(p, r) for p, r in new if p not in known]
    if added:
        pair_file.parent.mkdir(parents=True, exist_ok=True)
        lead = ""
        if pair_file.is_file():
            text = pair_file.read_text()
            if text and not text.endswith("\n"):
                lead = "\n"
        with pair_file.open("a") as f:
            f.write(lead + "".join(f"{p} => {r}\n" for p, r in added))
    print(
        f"saved {len(added)} entry(ies) to {pair_file}"
        + (f" ({len(new) - len(added)} already there)" if len(added) < len(new) else ""),
        file=sys.stderr,
    )
    return 0


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


def cmd_speak(
    source: list[str] | None,
    out: str | None,
    voice: str | None,
    speed: float,
    raw: bool = False,
    replaces: list[str] | None = None,
    pronounces: list[str] | None = None,
    save: bool = False,
    engine: str | None = None,
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
    if not raw:
        text = speakable(text)
    file_replaces = read_pair_file(SPEAK_REPLACEMENTS_FILE, "n0b ai speak")
    cli_replaces = parse_cli_pairs(replaces, "n0b ai speak")
    replace_pairs = file_replaces + cli_replaces
    file_pronounces = read_pair_file(SPEAK_PRONUNCIATIONS_FILE, "n0b ai speak")
    cli_pronounces = parse_cli_pairs(pronounces, "n0b ai speak")
    pronounce_pairs = file_pronounces + cli_pronounces
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
    if not text.strip():
        print("n0b ai speak: nothing to say after cleanup", file=sys.stderr)
        return 2
    out_path = Path(out).expanduser() if out else None
    if engine_name == "say":
        return _speak_say(plain_for_say(text), voice, speed, out_path)
    if voice is None:
        voice = DEFAULT_SPEAK_VOICE
    return _speak_kokoro(text, voice, speed, out_path)


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
        python = ensure_whisper()
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
