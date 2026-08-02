"""n0b CLI — argparse entry and dispatch."""
from __future__ import annotations

import argparse
import sys

from commands.ai_audio_cmd import cmd_audio
from commands.ai_image_cmd import cmd_image
from commands.ai_research_cmd import cmd_research
from commands.ai_speak_cmd import cmd_speak
from commands.ai_transcribe_cmd import cmd_transcribe
from commands.ai_video_cmd import cmd_video
from commands.az_cmd import cmd_tail
from commands.gpu_cmd import cmd_cuda, cmd_mb_free, cmd_mlx, cmd_mps
from commands.json_cmd import cmd_json
from commands.mqtt_cmd import cmd_pub, cmd_sub
from commands.ports_cmd import cmd_free, cmd_listen
from commands.quota_cmd import cmd_quota
from commands.secrets_cmd import cmd_get, cmd_set
from commands.video_cmd import cmd_gif, cmd_last_frame


def _add_image_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", help="Backend override (default: z-image)")
    p.add_argument(
        "--install",
        action="store_true",
        help="Create venv and install PyTorch/diffusers without generating",
    )
    p.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove <bin>/.venv and legacy per-command venvs/repos",
    )
    p.add_argument("--width", type=int, help="Output width in pixels (default: 1024)")
    p.add_argument("--height", type=int, help="Output height in pixels (default: 1024)")
    p.add_argument(
        "--16:9",
        dest="aspect_16_9",
        action="store_true",
        help="Use 1920x1088 (16:9, divisible by 16)",
    )
    p.add_argument(
        "--ref",
        action="append",
        default=[],
        metavar="PATH",
        help="Reference image for img2img; repeatable but only the first is used",
    )
    p.add_argument(
        "--strength",
        type=float,
        default=0.6,
        help="How much to transform --ref (0.0=preserve, 1.0=ignore; default: 0.6)",
    )
    p.add_argument(
        "-o",
        "--out",
        help="Output PNG path (default: z-image-<timestamp>.png)",
    )
    p.add_argument("prompt", nargs="*", default=[], help="Prompt text")


def _add_audio_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", help="Backend override (audioldm or bark)")
    p.add_argument(
        "--install",
        action="store_true",
        help="Install audio deps into <bin>/.venv without generating",
    )
    p.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove <bin>/.venv and legacy caches",
    )
    p.add_argument("-o", "--out", help="Output WAV path")
    p.add_argument("prompt", nargs="*", default=[], help="Prompt text")


def _parse_intermixed(p: argparse.ArgumentParser, argv: list[str]) -> argparse.Namespace:
    if argv[:1] == ["--"]:
        argv = argv[1:]
    return p.parse_intermixed_args(argv)


def parse_image_argv(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="n0b ai image", add_help=False)
    _add_image_args(p)
    return _parse_intermixed(p, argv)


def parse_audio_argv(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="n0b ai audio", add_help=False)
    _add_audio_args(p)
    return _parse_intermixed(p, argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="n0b",
        description="Neil's Bin — kitchen-sink utilities under one namespace.",
    )
    sub = parser.add_subparsers(dest="group", metavar="GROUP")
    sub.required = True

    json_p = sub.add_parser("json", help="Pretty-print JSON (stdlib json.tool)")
    json_p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to python -m json.tool",
    )

    az_p = sub.add_parser("az", help="Azure CLI helpers")
    az_sub = az_p.add_subparsers(dest="az_cmd", required=True)
    az_tail = az_sub.add_parser("tail", help="Tail Azure webapp logs by env alias")
    az_tail.add_argument(
        "env",
        help="Environment alias: dev, qa, staging, prod (and numeric variants)",
    )

    ports_p = sub.add_parser("ports", help="TCP port utilities")
    ports_sub = ports_p.add_subparsers(dest="ports_cmd", required=True)
    ports_sub.add_parser("free", help="Print an available TCP port number")
    ports_listen = ports_sub.add_parser("listen", help="Show process listening on a port")
    ports_listen.add_argument("port", type=int)

    gpu_p = sub.add_parser("gpu", help="GPU detection and memory")
    gpu_sub = gpu_p.add_subparsers(dest="gpu_cmd", required=True)
    gpu_cuda = gpu_sub.add_parser("cuda", help="Exit 0 if CUDA is available")
    gpu_cuda.add_argument("-v", "--verbose", action="store_true")
    gpu_mps = gpu_sub.add_parser("mps", help="Exit 0 if Apple MPS is available")
    gpu_mps.add_argument("-v", "--verbose", action="store_true")
    gpu_mlx = gpu_sub.add_parser("mlx", help="Exit 0 if Apple MLX (Apple Silicon) is available")
    gpu_mlx.add_argument("-v", "--verbose", action="store_true")
    gpu_sub.add_parser("mb-free", help="Print free GPU memory in MiB")

    secrets_p = sub.add_parser("secrets", help="Resolve secrets from env, ~/lib, or Keychain")
    secrets_sub = secrets_p.add_subparsers(dest="secrets_cmd", required=True)
    secrets_get = secrets_sub.add_parser("get", help="Print a secret value")
    secrets_get.add_argument("name", help="Environment variable name")
    secrets_set = secrets_sub.add_parser("set", help="Store a secret value")
    secrets_set.add_argument("name", help="Environment variable name")
    secrets_set.add_argument("value", nargs="?", help="Value (omit to read from stdin)")
    secrets_where = secrets_set.add_mutually_exclusive_group()
    secrets_where.add_argument("--dir", help="Base directory instead of ~/lib")
    secrets_where.add_argument(
        "--keychain", action="store_true", help="Store in the macOS Keychain"
    )
    secrets_where.add_argument(
        "--env-file", help="Upsert a NAME=value line in a dotenv file"
    )

    mqtt_p = sub.add_parser("mqtt", help="MQTT via mosquitto clients")
    mqtt_sub = mqtt_p.add_subparsers(dest="mqtt_cmd", required=True)
    mqtt_pub = mqtt_sub.add_parser("pub", help="Publish (mosquitto_pub)")
    mqtt_pub.add_argument("args", nargs=argparse.REMAINDER)
    mqtt_sub_p = mqtt_sub.add_parser("sub", help="Subscribe (mosquitto_sub)")
    mqtt_sub_p.add_argument("args", nargs=argparse.REMAINDER)

    ai_p = sub.add_parser("ai", help="AI generation and research")
    ai_sub = ai_p.add_subparsers(dest="ai_kind", required=True)
    ai_research = ai_sub.add_parser(
        "research",
        help="Deep research via gpt-5.6-sol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "OpenAI deep research (gpt-5.6-sol + web_search_preview). "
            "Default (--fanout absent or 1) is a single background job. "
            "--fanout [N] plans complementary angles (brief + MMR + reserved "
            "adversarial), runs N background jobs (bare --fanout uses Stage 0 "
            "recommended_fanout; clamped 1-8), and writes a merged brief under "
            ".files/research/fanout-<hash>/."
        ),
        epilog=(
            "examples:\n"
            "  n0b ai research what is X\n"
            "  n0b ai research --fanout 1 what is X\n"
            "  n0b ai research --fanout what is X\n"
            "  n0b ai research --fanout 4 what is X\n"
            "  n0b ai research --fanout 4 --plan-only what is X\n"
            "\n"
            "Put flags before the prompt (prompt is argparse REMAINDER). "
            "Bare --fanout asks Stage 0 for recommended_fanout (may be 1). "
            "Explicit --fanout N always wins. --plan-only prints the brief "
            "and selected sub-questions without submitting research jobs. "
            "See apps/n0b/docs/research.md."
        ),
    )
    ai_research.add_argument(
        "--fanout",
        nargs="?",
        const=0,
        default=None,
        type=int,
        metavar="N",
        help=(
            "Fan out into N research jobs (bare flag = Stage 0 "
            "recommended_fanout; 1 or omitted = single-shot; clamped 1-8)"
        ),
    )
    ai_research.add_argument(
        "--plan-only",
        action="store_true",
        help="Plan brief + MMR selection and exit without submitting research jobs",
    )
    ai_research.add_argument("prompt", nargs=argparse.REMAINDER)
    ai_speak = ai_sub.add_parser(
        "speak",
        help="Read text aloud or save speech to a file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Text-to-speech for arguments, a file, or stdin. On macOS the "
            "default engine is the built-in say(1) voice; use --engine kokoro "
            "for fully offline neural synthesis."
        ),
        epilog=(
            "examples:\n"
            "  n0b ai speak \"hello\"                 play on speakers\n"
            "  echo \"ship it\" | n0b ai speak         pipe stdin\n"
            "  n0b ai speak notes.md -o notes.m4a    save file (no playback)\n"
            "  n0b ai speak -v Samantha \"hi\" --save sticky macOS voice\n"
            "  n0b ai speak doc.md --engine kokoro -o out.wav  offline/file\n"
            "  n0b ai speak notes.md --pause-major 1.5 --pause-minor 0.8\n"
            "  n0b ai speak --replace '\\ba8s\\b => A eight S' --save\n"
            "\n"
            "Markdown is detected automatically: headings get section pauses "
            "(default 2s major / 1s minor) and bold/code get light emphasis. "
            "Use --flat for the old single-pass cleanup. Without -o/--out, "
            "audio goes to speakers. With -o, only a file is written (useful "
            "for tell --attach). See apps/n0b/docs/ai-speak.md."
        ),
    )
    ai_speak.add_argument(
        "text",
        nargs=argparse.REMAINDER,
        help="Inline text, file path, - for stdin, or omit to read stdin",
    )
    ai_speak.add_argument(
        "-o",
        "--out",
        help="Write audio file (.m4a/.aiff with say; .wav/.m4a with kokoro)",
    )
    ai_speak.add_argument(
        "-v",
        "--voice",
        help=(
            "Voice name: macOS voice for say (e.g. Samantha), Kokoro id for "
            "kokoro (e.g. af_nicole). Default reads ~/.config/n0b/speak-voice.txt"
        ),
    )
    ai_speak.add_argument(
        "--engine",
        choices=("auto", "say", "kokoro"),
        default="auto",
        help="TTS backend: auto prefers say on macOS, else kokoro (default: auto)",
    )
    ai_speak.add_argument(
        "--speed", type=float, default=1.0, help="Speech speed multiplier"
    )
    ai_speak.add_argument(
        "--raw", action="store_true", help="Skip markdown-to-prose cleanup"
    )
    ai_speak.add_argument(
        "--flat",
        action="store_true",
        help="Force single-pass speakable() cleanup (no section pauses)",
    )
    ai_speak.add_argument(
        "--pause-major",
        type=float,
        default=2.0,
        metavar="SEC",
        help="Silence before H1/H2 sections in seconds (default: 2.0)",
    )
    ai_speak.add_argument(
        "--pause-minor",
        type=float,
        default=1.0,
        metavar="SEC",
        help="Silence before H3–H6 sections in seconds (default: 1.0)",
    )
    ai_speak.add_argument(
        "--pause-para",
        type=float,
        default=0.4,
        metavar="SEC",
        help="Silence between paragraphs in seconds (default: 0.4)",
    )
    ai_speak.add_argument(
        "--no-emphasis",
        action="store_true",
        help="Disable bold/code emphasis (speed/slnc tweaks)",
    )
    ai_speak.add_argument(
        "--replace",
        action="append",
        default=[],
        dest="replaces",
        metavar="'TEXT => SPOKEN'",
        help=(
            "Regex + spoken form applied before synthesis. Merged with "
            "~/.config/n0b/speak-replacements.txt"
        ),
    )
    ai_speak.add_argument(
        "--pronounce",
        action="append",
        default=[],
        dest="pronounces",
        metavar="'WORD => IPA'",
        help=(
            "Regex + misaki IPA phonemes; matches become [word](/ipa/). "
            "Merged with ~/.config/n0b/speak-pronunciations.txt"
        ),
    )
    ai_speak.add_argument(
        "--save",
        action="store_true",
        help=(
            "Append --replace/--pronounce to their global files and/or "
            "persist --voice as the system default"
        ),
    )
    ai_transcribe = ai_sub.add_parser(
        "transcribe",
        help="Transcribe audio/video locally (Whisper; fancy video via Ollama vision)",
    )
    ai_transcribe.add_argument(
        "audio", nargs="?", help="Audio or video file (anything ffmpeg reads)"
    )
    ai_transcribe.add_argument(
        "--hint",
        "--hints",
        action="append",
        default=[],
        dest="hints",
        help=(
            "Vocabulary hint, repeatable; merged with "
            "~/.config/n0b/transcribe-hints.txt"
        ),
    )
    ai_transcribe.add_argument(
        "--language", help="Spoken language (e.g. en); default auto-detect"
    )
    ai_transcribe.add_argument(
        "--model", default="turbo", help="Whisper model (default: turbo)"
    )
    ai_transcribe.add_argument(
        "--engine",
        choices=("auto", "whisper", "mlx-whisper", "parakeet-mlx"),
        default="auto",
        help=(
            "STT backend: auto uses mlx-whisper on Apple Silicon, else whisper "
            "(default: auto)"
        ),
    )
    ai_transcribe.add_argument(
        "--flavor",
        choices=("auto", "plain", "fancy"),
        default="auto",
        help=(
            "Output style: auto uses fancy for video streams else plain; "
            "plain/fancy override detection (default: auto)"
        ),
    )
    ai_transcribe.add_argument(
        "--vision-model",
        default=None,
        help=(
            "Ollama vision model for --flavor fancy "
            "(default: N0B_TRANSCRIBE_VISION_MODEL or qwen3.6)"
        ),
    )
    ai_transcribe.add_argument(
        "--replace",
        action="append",
        default=[],
        dest="replaces",
        metavar="'WRONG => RIGHT'",
        help=(
            "Regex + correction; matches get annotated after transcription. "
            "Merged with ~/.config/n0b/transcribe-replacements.txt"
        ),
    )
    ai_transcribe.add_argument(
        "--save",
        action="store_true",
        help="Append the given --hint/--replace values to their global files",
    )
    ai_transcribe.add_argument(
        "--condition-on-previous",
        action="store_true",
        help=(
            "Let Whisper condition each window on its prior text "
            "(default off; on can loop on silence/noise)"
        ),
    )
    ai_image = ai_sub.add_parser(
        "image",
        help="Generate images locally with Z-Image-Turbo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Text-to-image with Z-Image-Turbo. Pass --ref to transform a reference "
            "image with a prompt (img2img); only the first --ref is used."
        ),
        epilog=(
            "examples:\n"
            "  n0b ai image \"a red fox in snow\"\n"
            "  n0b ai image photo.jpg \"oil painting\" --ref photo.jpg\n"
            "  n0b ai image \"cinematic portrait\" --ref face.png --strength 0.35 -o out.png\n"
            "\n"
            "First run auto-installs deps into <bin>/.venv (shared repo venv). "
            "Use --install to prep ahead of time; --uninstall to remove."
        ),
    )
    _add_image_args(ai_image)
    ai_audio = ai_sub.add_parser(
        "audio",
        help="Generate audio (default model: audioldm)",
    )
    _add_audio_args(ai_audio)
    ai_video = ai_sub.add_parser(
        "video",
        help="Generate videos — LTX-Video 1/2, MLX on Apple Silicon (default: auto)",
    )
    ai_video.add_argument(
        "--model",
        help=(
            "Backend override (video: ltx-video auto, ltx-2, ltx-1; "
            "image: z-image; audio: audioldm, bark)"
        ),
    )
    ai_video.add_argument("args", nargs=argparse.REMAINDER)

    video_p = sub.add_parser("video", help="Video file utilities")
    video_sub = video_p.add_subparsers(dest="video_cmd", required=True)
    last_frame = video_sub.add_parser("last-frame", help="Extract last frame with ffmpeg")
    last_frame.add_argument("video")
    last_frame.add_argument("-o", "--output")
    gif_p = video_sub.add_parser(
        "gif",
        help="Convert a video to an animated GIF (Surfey-style palette)",
    )
    gif_p.add_argument("video", help="Input video file")
    gif_p.add_argument("-o", "--output", help="Output GIF path (default: <stem>.gif)")
    gif_p.add_argument(
        "--preset",
        choices=("thumb", "small"),
        default="thumb",
        help=(
            "thumb: adaptive ≤1 FPS / ≤50 frames, 320px, 64 colors (default); "
            "small: 8 FPS, 800px, 32 colors"
        ),
    )
    gif_p.add_argument("--fps", type=float, help="Override GIF frame rate")
    gif_p.add_argument("--width", type=int, help="Override GIF max width in pixels")
    gif_p.add_argument("--colors", type=int, help="Override palette max colors")
    gif_p.add_argument(
        "--max-frames",
        type=int,
        help="Override max frames for thumb adaptive FPS (default: 50)",
    )

    quota_p = sub.add_parser("quota", help="Check AI tool usage quotas")
    quota_p.add_argument(
        "tools",
        nargs="*",
        metavar="TOOL",
        help="Tool id(s) to query (default: all installed). Supported: agy",
    )
    quota_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    quota_p.add_argument(
        "--raw",
        action="store_true",
        help="Include raw Antigravity API payload in JSON output",
    )

    return parser


def dispatch(args: argparse.Namespace) -> int:
    group = args.group
    if group == "json":
        rest = args.args
        if rest[:1] == ["--"]:
            rest = rest[1:]
        return cmd_json(rest)
    if group == "az":
        if args.az_cmd == "tail":
            return cmd_tail(args.env)
    if group == "ports":
        if args.ports_cmd == "free":
            return cmd_free()
        if args.ports_cmd == "listen":
            return cmd_listen(args.port)
    if group == "gpu":
        if args.gpu_cmd == "cuda":
            return cmd_cuda(args.verbose)
        if args.gpu_cmd == "mps":
            return cmd_mps(args.verbose)
        if args.gpu_cmd == "mlx":
            return cmd_mlx(args.verbose)
        if args.gpu_cmd == "mb-free":
            return cmd_mb_free()
    if group == "secrets":
        if args.secrets_cmd == "get":
            return cmd_get(args.name)
        if args.secrets_cmd == "set":
            return cmd_set(
                args.name,
                args.value,
                base_dir=args.dir,
                keychain=args.keychain,
                env_file=args.env_file,
            )
    if group == "mqtt":
        rest = args.args
        if rest[:1] == ["--"]:
            rest = rest[1:]
        if args.mqtt_cmd == "pub":
            return cmd_pub(rest)
        if args.mqtt_cmd == "sub":
            return cmd_sub(rest)
    if group == "ai":
        if args.ai_kind == "research":
            return cmd_research(
                args.prompt, fanout=args.fanout, plan_only=args.plan_only
            )
        if args.ai_kind == "speak":
            return cmd_speak(
                args.text,
                args.out,
                args.voice,
                args.speed,
                raw=args.raw,
                flat=args.flat,
                replaces=args.replaces,
                pronounces=args.pronounces,
                save=args.save,
                engine=args.engine,
                pause_major=args.pause_major,
                pause_minor=args.pause_minor,
                pause_para=args.pause_para,
                emphasis=not args.no_emphasis,
            )
        if args.ai_kind == "transcribe":
            return cmd_transcribe(
                args.audio,
                args.hints,
                args.language,
                args.model,
                save=args.save,
                replaces=args.replaces,
                flavor=args.flavor,
                vision_model=args.vision_model,
                condition_on_previous=args.condition_on_previous,
                engine=args.engine,
            )
        if args.ai_kind == "image":
            return cmd_image(
                args.model,
                args.prompt,
                args.ref,
                args.strength,
                args.out,
                width=args.width,
                height=args.height,
                aspect_16_9=args.aspect_16_9,
                install=args.install,
                uninstall=args.uninstall,
            )
        if args.ai_kind == "video":
            rest = args.args
            if rest[:1] == ["--"]:
                rest = rest[1:]
            return cmd_video(args.model, rest)
        if args.ai_kind == "audio":
            if args.install:
                return cmd_audio(args.model, ["--install"])
            if args.uninstall:
                return cmd_audio(args.model, ["--uninstall"])
            rest = list(args.prompt)
            if args.out:
                rest.extend(["-o", args.out])
            return cmd_audio(args.model, rest)
    if group == "video":
        if args.video_cmd == "last-frame":
            return cmd_last_frame(args.video, args.output)
        if args.video_cmd == "gif":
            return cmd_gif(
                args.video,
                args.output,
                preset=args.preset,
                fps=args.fps,
                width=args.width,
                colors=args.colors,
                max_frames=args.max_frames,
            )
    if group == "quota":
        return cmd_quota(args.tools, as_json=args.json, raw=args.raw)
    print(f"n0b: unhandled command group {group!r}", file=sys.stderr)
    return 2


def _inject_bare_fanout_default(argv: list[str]) -> list[str]:
    """Turn bare ``--fanout`` into ``--fanout 0`` (auto) so the prompt is not eaten.

    ``0`` means Stage 0 picks ``recommended_fanout``; an explicit N always wins.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--fanout":
            out.append(a)
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and not nxt.startswith("-"):
                try:
                    int(nxt)
                except ValueError:
                    out.append("0")
                else:
                    out.append(nxt)
                    i += 1
            else:
                out.append("0")
        else:
            out.append(a)
        i += 1
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == "ai" and "-h" not in argv and "--help" not in argv:
        if argv[1] == "image":
            a = parse_image_argv(argv[2:])
            return cmd_image(
                a.model,
                a.prompt,
                a.ref,
                a.strength,
                a.out,
                width=a.width,
                height=a.height,
                aspect_16_9=a.aspect_16_9,
                install=a.install,
                uninstall=a.uninstall,
            )
        if argv[1] == "audio":
            a = parse_audio_argv(argv[2:])
            if a.install:
                return cmd_audio(a.model, ["--install"])
            if a.uninstall:
                return cmd_audio(a.model, ["--uninstall"])
            rest = list(a.prompt)
            if a.out:
                rest.extend(["-o", a.out])
            return cmd_audio(a.model, rest)
    if len(argv) >= 2 and argv[0] == "ai" and argv[1] == "research":
        argv = _inject_bare_fanout_default(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)
