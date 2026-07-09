#!/usr/bin/env python3
"""h4l — Hall chat rooms for multi-agent coordination.

Standalone CLI with optional a8s invoke wiring. State lives under
<root>/.chatrooms/; notifications use `tell` on PATH.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dispatch import dispatch_slash
from notify import resolve_tell_fn, simulate_enabled
from rooms import RoomStore


def _resolve_root(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.cwd().resolve()


def cmd_dispatch(args: argparse.Namespace) -> int:
    if not args.from_agent:
        print("dispatch: --from is required", file=sys.stderr)
        return 2
    if not args.node:
        print("dispatch: --node is required", file=sys.stderr)
        return 2
    if args.message is None:
        print("dispatch: --message is required", file=sys.stderr)
        return 2
    store = RoomStore(_resolve_root(args.root))
    tell_fn = resolve_tell_fn(
        notify=args.notify,
        simulate=simulate_enabled(args.simulate_tell),
    )
    return dispatch_slash(
        store,
        sender=args.from_agent,
        node=args.node,
        message=args.message,
        tell_fn=tell_fn,
    )


def cmd_clear(args: argparse.Namespace) -> int:
    store = RoomStore(_resolve_root(args.root))
    if args.all:
        count = store.clear_all()
        print(f"cleared {count} room(s)")
        return 0
    if args.older_than is None:
        print("clear: specify --older-than SECS or --all", file=sys.stderr)
        return 2
    if args.older_than <= 0:
        print("clear: --older-than must be positive", file=sys.stderr)
        return 2
    removed = store.clear_older_than(args.older_than)
    if removed:
        print(f"cleared {len(removed)} room(s): {', '.join(removed)}")
    else:
        print("cleared 0 room(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="h4l",
        description="Hall chat rooms — slash-command CLI for multi-agent chat.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    dispatch_p = sub.add_parser(
        "dispatch",
        help="Handle one slash-command message (a8s invoke or direct use).",
    )
    dispatch_p.add_argument(
        "--root",
        help="Agent root containing .chatrooms/ (default: cwd).",
    )
    dispatch_p.add_argument("--from", dest="from_agent", required=True)
    dispatch_p.add_argument(
        "--node",
        required=True,
        help="This hall node's a8s name (embedded in notification footers).",
    )
    dispatch_p.add_argument("--message", required=True)
    dispatch_p.add_argument(
        "--simulate-tell",
        action="store_true",
        help="Print would-be tell calls to stderr instead of invoking tell "
        "(also H4L_SIMULATE_TELL=1).",
    )
    dispatch_p.add_argument(
        "--no-notify",
        dest="notify",
        action="store_false",
        default=True,
        help="Drop tell fan-out entirely (unit tests).",
    )
    dispatch_p.set_defaults(func=cmd_dispatch)

    clear_p = sub.add_parser("clear", help="Delete stale or all chat rooms.")
    clear_p.add_argument("--root", help="Agent root (default: cwd).")
    clear_p.add_argument(
        "--older-than",
        type=float,
        metavar="SECS",
        help="Remove rooms idle longer than SECS.",
    )
    clear_p.add_argument("--all", action="store_true", help="Remove every room.")
    clear_p.set_defaults(func=cmd_clear)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
