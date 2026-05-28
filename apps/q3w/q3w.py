"""q3w — NLP to bash command. LLM must produce a program, not speak.

Uses l9m to generate a bash command from natural language, then executes it.
The LLM's only output channel is structured: an executable command.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

L9M_DIR = Path(__file__).resolve().parent.parent / "l9m"
sys.path.insert(0, str(L9M_DIR))

import l9m


def main() -> int:
    argv = sys.argv[1:]
    dry_run = False
    words = []

    for arg in argv:
        if arg in ("--dry-run", "-n"):
            dry_run = True
        else:
            words.append(arg)

    if not words:
        print("usage: q3w <natural language command>", file=sys.stderr)
        return 1

    prompt = " ".join(words)
    shell = os.environ.get("SHELL", "/bin/bash")
    instruction = f"I am using {shell}"

    model = l9m.resolve_model()
    full_prompt = l9m.assemble_prompt(prompt, "bash", instruction, "")

    # Capture output instead of streaming
    old_stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        output = l9m.generate(model, full_prompt, silent=True)
    finally:
        sys.stdout.close()
        sys.stdout = old_stdout

    cmd = output.strip()
    if not cmd:
        print("error: LLM produced empty output", file=sys.stderr)
        return 1

    if dry_run:
        print(cmd)
        return 0

    return subprocess.call([shell, "-c", cmd])


if __name__ == "__main__":
    sys.exit(main())
