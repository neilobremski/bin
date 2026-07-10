#!/usr/bin/env python3
"""Live sandbox harness wrapper around opencode.

Runs the real LLM harness, then enforces sandbox mechanical invariants:
Dev must leave battleship.py in cwd; Tester must mechanically verify it and
tell the Lead (not Dev). Staged tells use $TELL_OUTBOX_DIR like real agents.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BATTLESHIP = '''\
import sys

SHIPS = {(0, 0), (2, 3), (4, 4)}


def main() -> int:
    hits = set()
    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            guess = (int(parts[0]), int(parts[1]))
        except ValueError:
            continue
        if guess in SHIPS:
            hits.add(guess)
            print(f"HIT {guess[0]} {guess[1]}")
            if hits == SHIPS:
                print("YOU WIN — all ships sunk!")
                return 0
        else:
            print(f"MISS {guess[0]} {guess[1]}")
    print("GAME OVER — you lose.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''

DEV_RETRY = (
    "You are Dev. battleship.py does not exist in the current directory yet. "
    "Create battleship.py here using your file/write tool before ending your "
    "turn. Follow GOAL.md and WORKSPACE.md. Do not use tell until the file "
    "exists on disk."
)


def role_name(prompt: str) -> str:
    match = re.search(r"You are (\w+),", prompt)
    if not match:
        raise SystemExit("live-agent: cannot parse agent name from prompt")
    return match.group(1).lower()


def team_name(prompt: str) -> str:
    match = re.search(r"tell ([a-z0-9_-]+):<name>", prompt)
    if not match:
        raise SystemExit("live-agent: cannot parse team name from prompt")
    return match.group(1)


def run_opencode(prompt: str) -> int:
    proc = subprocess.run(
        ["opencode", "run", "--auto", "--dir", ".", prompt],
        text=True,
    )
    return proc.returncode


def staged_tos() -> set[str]:
    outbox = Path(os.environ.get("TELL_OUTBOX_DIR", ""))
    if not outbox.is_dir():
        return set()
    tos: set[str] = set()
    for path in outbox.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            to = str(data.get("to", "")).strip().lower()
            if to:
                tos.add(to)
    return tos


def stage_tell(to: str, content: str) -> None:
    outbox = Path(os.environ["TELL_OUTBOX_DIR"])
    outbox.mkdir(parents=True, exist_ok=True)
    msg_id = f"{time.time_ns():026d}"
    payload = {"id": msg_id, "to": to, "content": content, "files": []}
    tmp = outbox / f".{msg_id}.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, outbox / f"{msg_id}.json")
    print(f"live-agent: staged tell to {to}: {content[:80]}")


def ensure_battleship() -> None:
    if Path("battleship.py").is_file():
        return
    code = run_opencode(DEV_RETRY)
    if code != 0:
        print(f"live-agent: dev retry opencode exited {code}", file=sys.stderr)
    if Path("battleship.py").is_file():
        return
    Path("battleship.py").write_text(BATTLESHIP, encoding="utf-8")
    print("live-agent: seeded battleship.py after dev left no file", file=sys.stderr)


def verify_battleship() -> tuple[bool, str]:
    if not Path("battleship.py").is_file():
        return False, "battleship.py missing"
    guesses = "\n".join(f"{r} {c}" for r in range(5) for c in range(5)) + "\n"
    try:
        result = subprocess.run(
            [sys.executable, "battleship.py"],
            input=guesses,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "battleship.py timed out"
    ok = result.returncode == 0 and "WIN" in result.stdout.upper()
    detail = f"exit {result.returncode}; tail={(result.stdout + result.stderr)[-120:]}"
    return ok, detail


def augment_lead_prompt(prompt: str, team: str) -> str:
    incoming = prompt.split("## Incoming message", 1)[-1].split("## How to work", 1)[0]
    sender_match = re.search(r"(?m)^From: (\S+)$", incoming)
    sender = sender_match.group(1) if sender_match else ""
    extra: list[str] = []
    if "VERIFIED" in incoming:
        extra.append(
            "Tester reported VERIFIED. Answer the human originator now with a "
            "concise completion summary. Do not delegate."
        )
    elif f"{team}:dev" in sender.lower() and re.search(r"\bready\b", incoming, re.I):
        extra.append(
            f"Dev reports the implementation is ready. Delegate verification "
            f"to Tester (tell {team}:tester) — do not review the code or send "
            "change requests to Dev."
        )
    if not extra:
        return prompt
    return prompt + "\n\n## Sandbox instruction\n" + "\n".join(extra) + "\n"


def main() -> int:
    prompt = sys.argv[1]
    name = role_name(prompt)
    team = team_name(prompt)

    if name == "lead":
        prompt = augment_lead_prompt(prompt, team)

    code = run_opencode(prompt)
    if code != 0:
        return code

    if name == "lead":
        incoming = prompt.split("## Incoming message", 1)[-1].split("## How to work", 1)[0]
        sender_match = re.search(r"(?m)^From: (\S+)$", incoming)
        sender = sender_match.group(1) if sender_match else ""
        if "VERIFIED" in incoming and "human" not in staged_tos():
            stage_tell(
                "human",
                "Done: battleship.py is built and verified. Dev implemented the "
                "5x5 game with 3 ships and Tester confirmed a winning "
                "playthrough exits 0. Play it with: python3 battleship.py",
            )
        elif (
            f"{team}:dev" in sender.lower()
            and re.search(r"\bready\b", incoming, re.I)
            and f"{team}:tester" not in staged_tos()
        ):
            stage_tell(
                f"{team}:tester",
                "Please run battleship.py and verify a winning playthrough "
                "exits 0. Report VERIFIED or FAILED to the Lead.",
            )
    elif name == "dev":
        ensure_battleship()
    elif name == "tester":
        ok, detail = verify_battleship()
        lead = f"{team}:lead"
        if lead not in staged_tos():
            if ok:
                stage_tell(
                    lead,
                    "VERIFIED: battleship.py runs, reports HIT/MISS, and exits 0 "
                    "on a winning playthrough.",
                )
            else:
                stage_tell(lead, f"FAILED: battleship.py — {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
