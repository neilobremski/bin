"""tell-proxy — per-turn interception of the `tell` verb.

Dispatch drops an executable `tell` shim at the front of the harness
subprocess PATH; the shim execs `r4t tell-proxy --team <node> --agent
<name> --turn <turn-file> -- <recipient> <message...>`, which:

1. enforces the tier's per-turn send quota (turn-file counter),
2. ensures the task header `[r4t task=... hop=...]` is present on the
   outgoing message (hop incremented), so header propagation no longer
   relies on the LLM copying it,
3. appends the outbound message to the agent's conversation history, and
4. execs the real `tell` recorded at dispatch time.

Turn modes: `real` execs the recorded tell binary; `simulate` prints the
would-be tell to stderr; `drop` swallows it (unit tests). A missing turn
file (shell-out after the turn ended, or a crashed turn's stale shim)
passes through to the first `tell` on PATH outside the shim dir.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import state
import tasks

QUOTA_MESSAGE = (
    "r4t: send quota exhausted for this turn — remaining messages were not sent"
)
OUTBOUND_BODY_MAX = 2000


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def _read_turn_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _find_real_tell_excluding(shim: Path) -> str | None:
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).resolve() == shim.resolve():
                continue
        except OSError:
            pass
        candidate = Path(entry) / "tell"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _forward(real_tell: str, recipient: str, message: str) -> int:
    try:
        os.execv(real_tell, [real_tell, recipient, message])
    except OSError as e:
        _err(f"r4t: cannot exec tell at {real_tell}: {e}")
        return 1
    return 0  # unreachable


def run_tell_proxy(team: str, agent: str, turn_file: str, rest: list[str]) -> int:
    args = list(rest)
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        _err("r4t tell-proxy: missing recipient")
        return 2
    recipient = args[0]
    message = " ".join(args[1:]).strip()
    if not message and not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    if not message:
        _err("r4t tell-proxy: empty message")
        return 2

    turn_path = Path(turn_file)
    turn = _read_turn_file(turn_path)
    if turn is None:
        _err("r4t: no active turn — forwarding tell without task bookkeeping")
        real = _find_real_tell_excluding(state.shim_dir(team, agent))
        if real is None:
            _err("r4t: tell not found on PATH")
            return 1
        return _forward(real, recipient, message)

    remaining = int(turn.get("sends_remaining", 0) or 0)
    if remaining <= 0:
        _err(QUOTA_MESSAGE)
        return 1
    turn["sends_remaining"] = remaining - 1
    state.atomic_write_json(turn_path, turn)

    task_id, _hop, body = tasks.parse_header(message)
    if task_id is None:
        body = message
        header = tasks.format_header(
            str(turn.get("task", "")), int(turn.get("hop", 0) or 0) + 1
        )
        message = f"{header} {message}"

    state.append_history(
        team,
        agent,
        f"## {state.utc_now()} to {recipient}\n\n{body[:OUTBOUND_BODY_MAX]}",
    )

    mode = str(turn.get("mode", "real"))
    if mode == "drop":
        return 0
    real_tell = str(turn.get("real_tell", "") or "")
    if mode == "simulate" or not real_tell:
        _err(f"r4t> tell {recipient}: {message}")
        return 0
    return _forward(real_tell, recipient, message)
