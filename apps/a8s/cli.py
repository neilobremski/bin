"""a8s CLI — the COMMANDS table, dispatcher, and main argparse entry."""
from __future__ import annotations

import argparse
import sys

from commands import (
    cmd_add,
    cmd_agents,
    cmd_alias,
    cmd_aliases,
    cmd_define,
    cmd_discover,
    cmd_drain,
    cmd_exit,
    cmd_health,
    cmd_install,
    cmd_kill,
    cmd_logs,
    cmd_ls,
    cmd_remote,
    cmd_remove,
    cmd_run,
    cmd_start,
    cmd_step,
    cmd_stop,
    cmd_storage,
    cmd_tell,
    cmd_unalias,
    cmd_unremote,
    cmd_unstorage,
)


COMMANDS: list[tuple[str, str, str]] = [
    ("add",      "<name> <dir> [<def>]",      "Register an agent."),
    ("remove",   "<name>",                    "Unregister an agent and delete its mailbox."),
    ("agents",   "",                          "List registered agents."),
    ("discover", "<path>",                    "Scan a path for agents and suggest `add` commands."),
    ("define",   "<name> [<path>]",           "Show or set an agent's command definition."),
    ("alias",    "[<name> [<member>]]",       "Group agents under an alias name; show one with `<name>`."),
    ("unalias",  "<alias> [<member>]",        "Remove a member from an alias, or the whole alias."),
    ("aliases",  "",                          "List aliases and their members."),
    ("start",    "<name>",                    "Run an agent in the background."),
    ("run",      "<name> [--drain <sec>]",     "Run an agent in the foreground."),
    ("step",     "<name>",                    "Run an agent for one pass and exit."),
    ("stop",     "<name>",                    "Stop a running agent."),
    ("kill",     "<name>",                    "Force-stop a running agent."),
    ("exit",     "",                          "Stop every running agent."),
    ("ls",       "",                          "List running agents."),
    ("tell",     "<name> [<message>]",       "Send a message to an agent or alias."),
    ("drain",    "<name>",                   "Move local inbox to trash without invoking."),
    ("logs",     "<name>... [--tail N] [-f]", "Show per-agent logs."),
    ("remote",   "[<name> [<broker> <topic> [--<k> <v> ...]]]", "List, show, or set a cross-machine remote."),
    ("unremote", "<name>",                    "Remove a configured remote."),
    ("storage",  "[<name> [<url> [--<k> <v> ...]]]",            "List, show, or set a cross-cluster file storage service."),
    ("unstorage","<name>",                    "Remove a configured storage service."),
    ("install",  "[path] [--global]",        "Install skills into an agent dir (default CWD) or user home."),
    ("health",   "",                          "Test connectivity of remotes and storage services."),
]

KNOWN_COMMANDS = {name for name, _, _ in COMMANDS}


def _format_commands(rows: list[tuple[str, str, str]], indent: int = 2) -> str:
    headers = [(n + " " + a).strip() for n, a, _ in rows]
    width = max(len(h) for h in headers)
    return "\n".join(
        f"{' ' * indent}{header.ljust(width)}    {help_text}"
        for header, (_, _, help_text) in zip(headers, rows)
    )


CLI_EPILOG = "Commands:\n" + _format_commands(COMMANDS)


def dispatch(cmd: str, args: list[str], interval: float) -> int:
    if cmd == "add":
        return cmd_add(args)
    if cmd == "remove":
        return cmd_remove(args)
    if cmd == "agents":
        return cmd_agents()
    if cmd == "discover":
        return cmd_discover(args)
    if cmd == "define":
        return cmd_define(args)
    if cmd == "alias":
        return cmd_alias(args)
    if cmd == "unalias":
        return cmd_unalias(args)
    if cmd == "aliases":
        return cmd_aliases()
    if cmd == "start":
        return cmd_start(args)
    if cmd == "run":
        return cmd_run(args, interval)
    if cmd == "step":
        return cmd_step(args, interval)
    if cmd == "stop":
        return cmd_stop(args)
    if cmd == "kill":
        return cmd_kill(args)
    if cmd == "exit":
        return cmd_exit()
    if cmd == "ls":
        return cmd_ls()
    if cmd == "tell":
        return cmd_tell(args)
    if cmd == "drain":
        return cmd_drain(args)
    if cmd == "install":
        return cmd_install(args)
    if cmd == "logs":
        return cmd_logs(args)
    if cmd == "remote":
        return cmd_remote(args)
    if cmd == "unremote":
        return cmd_unremote(args)
    if cmd == "storage":
        return cmd_storage(args)
    if cmd == "unstorage":
        return cmd_unstorage(args)
    if cmd == "health":
        return cmd_health()
    raise ValueError(f"unknown command: {cmd!r}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="a8s",
        description="Agent Infinity System — route messages between Claude / Gemini / Codex projects.",
        epilog=CLI_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--interval", type=float, default=1.0, help="loop poll interval seconds (default: 1.0)")
    parser.add_argument("command", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command in KNOWN_COMMANDS:
        return dispatch(args.command, args.rest, args.interval)

    print(f"unknown command: {args.command!r}", file=sys.stderr)
    return 2
