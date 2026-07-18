"""n0b CLI — argparse entry and dispatch."""
from __future__ import annotations

import argparse
import sys

from commands.ai_cmd import cmd_ai, cmd_research, cmd_speak, cmd_transcribe
from commands.az_cmd import cmd_tail
from commands.gpu_cmd import cmd_cuda, cmd_mb_free, cmd_mlx, cmd_mps
from commands.json_cmd import cmd_json
from commands.mqtt_cmd import cmd_pub, cmd_sub
from commands.ports_cmd import cmd_free, cmd_listen
from commands.quota_cmd import cmd_quota
from commands.secrets_cmd import cmd_get, cmd_set
from commands.video_cmd import cmd_last_frame


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
    ai_research = ai_sub.add_parser("research", help="Deep research via o4-mini-deep-research")
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
            "  n0b ai speak --replace '\\ba8s\\b => A eight S' --save\n"
            "\n"
            "Without -o/--out, audio goes to speakers. With -o, only a file "
            "is written (useful for tell --attach). Replacements and "
            "pronunciations live in ~/.config/n0b/speak-*.txt; see "
            "apps/n0b/docs/ai-speak.md."
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
        "transcribe", help="Transcribe an audio file locally with Whisper"
    )
    ai_transcribe.add_argument(
        "audio", nargs="?", help="Audio file (anything ffmpeg reads)"
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
    for kind, help_text in (
        ("image", "Generate images (default model: z-image)"),
        ("video", "Generate videos — LTX-Video 1/2, MLX on Apple Silicon (default: auto)"),
        ("audio", "Generate audio (default model: audioldm)"),
    ):
        p = ai_sub.add_parser(kind, help=help_text)
        p.add_argument(
            "--model",
            help=(
                "Backend override (video: ltx-video auto, ltx-2, ltx-1; "
                "image: z-image; audio: audioldm, bark)"
            ),
        )
        p.add_argument("args", nargs=argparse.REMAINDER)

    video_p = sub.add_parser("video", help="Video file utilities")
    video_sub = video_p.add_subparsers(dest="video_cmd", required=True)
    last_frame = video_sub.add_parser("last-frame", help="Extract last frame with ffmpeg")
    last_frame.add_argument("video")
    last_frame.add_argument("-o", "--output")

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
            return cmd_research(args.prompt)
        if args.ai_kind == "speak":
            return cmd_speak(
                args.text,
                args.out,
                args.voice,
                args.speed,
                raw=args.raw,
                replaces=args.replaces,
                pronounces=args.pronounces,
                save=args.save,
                engine=args.engine,
            )
        if args.ai_kind == "transcribe":
            return cmd_transcribe(
                args.audio,
                args.hints,
                args.language,
                args.model,
                save=args.save,
                replaces=args.replaces,
            )
        rest = args.args
        if rest[:1] == ["--"]:
            rest = rest[1:]
        return cmd_ai(args.ai_kind, args.model, rest)
    if group == "video":
        if args.video_cmd == "last-frame":
            return cmd_last_frame(args.video, args.output)
    if group == "quota":
        return cmd_quota(args.tools, as_json=args.json, raw=args.raw)
    print(f"n0b: unhandled command group {group!r}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)
