#!/usr/bin/env python3
"""Deterministic fake agent for `r4t sandbox --fake`.

Parses the r4t prompt it is invoked with, plays its roster role (Lead
delegates and answers, Dev writes battleship.py, Tester runs it), and sends
messages by writing tell-shaped envelopes into $TELL_OUTBOX_DIR — the same
staging contract a real `tell` uses, with zero LLM calls.
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


VERIFIED_RE = re.compile(r"VERIFIED:", re.I)

TEAM = {"lead", "dev", "tester", "owner"}


def send(to: str, content: str) -> None:
    outbox = Path(os.environ["TELL_OUTBOX_DIR"])
    outbox.mkdir(parents=True, exist_ok=True)
    msg_id = f"{time.time_ns():026d}"
    payload = {"id": msg_id, "to": to, "content": content, "files": []}
    tmp = outbox / f".{msg_id}.tmp"
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, outbox / f"{msg_id}.json")
    print(f"fake-agent: sent to {to}: {content[:80]}")


def first_external_sender(prompt: str) -> str:
    for name in re.findall(r"(?m)^(?:## \S+ from|From:) (\S+)", prompt):
        if ":" not in name and name != "r4t" and name.lower() not in TEAM:
            return name
    return "human"


def main() -> int:
    prompt = sys.argv[1]
    name = re.search(r"You are (\w+),", prompt).group(1).lower()
    incoming = prompt.split("## Messages since your last turn", 1)[-1].split(
        "## How to work", 1
    )[0]
    sender_match = re.search(r"(?m)^From: (\S+)", incoming)
    sender = sender_match.group(1) if sender_match else "unknown"
    print(f"fake-agent: {name} woken by {sender}")

    if name == "lead":
        if (VERIFIED_RE.search(incoming) and "tester" in incoming.lower()) or "gone quiet" in incoming:
            originator = first_external_sender(prompt)
            send(
                originator,
                "Done: battleship.py is built and verified. Dev implemented the "
                "5x5 game with 3 ships and Tester confirmed a winning "
                "playthrough exits 0. Play it with: python3 battleship.py",
            )
        elif "FAILED" in incoming:
            send("dev", "Tester reports the game FAILED — please fix battleship.py.")
        else:
            # Ack the human AND delegate in the same turn — the real leader
            # pattern that must not close the task and kill the delegation.
            ack_to = sender if sender.lower() not in TEAM else first_external_sender(prompt)
            send(
                ack_to,
                "Acknowledged — delegating the battleship build to Dev now; "
                "full report when Tester verifies.",
            )
            send(
                "dev",
                "Please build the terminal battleship game from GOAL.md as "
                "battleship.py (5x5 grid, 3 ships, stdin guesses `row col`, "
                "exit 0 on win). Hand it to Tester when written.",
            )
    elif name == "dev":
        Path("battleship.py").write_text(BATTLESHIP, encoding="utf-8")
        print("fake-agent: wrote battleship.py")
        send(
            "tester",
            "battleship.py is written — please run it and verify a winning "
            "playthrough exits 0, then report to the Lead.",
        )
    elif name == "tester":
        guesses = "\n".join(f"{r} {c}" for r in range(5) for c in range(5)) + "\n"
        result = subprocess.run(
            [sys.executable, "battleship.py"],
            input=guesses,
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(f"fake-agent: battleship.py exited {result.returncode}")
        if result.returncode == 0 and "WIN" in result.stdout:
            send(
                "lead",
                "VERIFIED: battleship.py runs, reports HIT/MISS, and exits 0 "
                "on a winning playthrough.",
            )
        else:
            send(
                "lead",
                f"FAILED: battleship.py exited {result.returncode}: "
                f"{(result.stdout + result.stderr)[-200:]}",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
