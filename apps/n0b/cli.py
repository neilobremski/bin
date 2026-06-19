"""n0b CLI — argparse entry and dispatch."""
from __future__ import annotations

import argparse
import sys

from commands.ai_cmd import cmd_ai, cmd_research
from commands.az_cmd import cmd_tail
from commands.gpu_cmd import cmd_cuda, cmd_mb_free, cmd_mlx, cmd_mps
from commands.json_cmd import cmd_json
from commands.mqtt_cmd import cmd_pub, cmd_sub
from commands.ports_cmd import cmd_free, cmd_listen
from commands.secrets_cmd import cmd_get
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

    secrets_p = sub.add_parser("secrets", help="Resolve secrets from env or ~/lib")
    secrets_sub = secrets_p.add_subparsers(dest="secrets_cmd", required=True)
    secrets_get = secrets_sub.add_parser("get", help="Print a secret value")
    secrets_get.add_argument("name", help="Environment variable name")

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
        rest = args.args
        if rest[:1] == ["--"]:
            rest = rest[1:]
        return cmd_ai(args.ai_kind, args.model, rest)
    if group == "video":
        if args.video_cmd == "last-frame":
            return cmd_last_frame(args.video, args.output)
    print(f"n0b: unhandled command group {group!r}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)
