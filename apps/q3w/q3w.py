"""q3w — NLP to bash command. LLM must produce a program, not speak.

Uses l9m to generate a bash command from natural language, then executes it.
The LLM's only output channel is structured: an executable command.

Safety: command is printed, validated as parseable bash, and a brief delay
gives the user time to CTRL+C before execution.

Default behavior: generate → print → validate → delay → execute.
Use -n/--dry-run to print without executing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

L9M_DIR = Path(__file__).resolve().parent.parent / "l9m"
sys.path.insert(0, str(L9M_DIR))

import l9m

GRAY = "\033[0;90m"
RESET = "\033[0m"


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

    # Capture output (suppress streaming to stdout)
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

    # Always show what's about to run
    is_tty = sys.stderr.isatty()
    if is_tty:
        print(f"{GRAY}$ {cmd}{RESET}", file=sys.stderr)
    else:
        print(f"$ {cmd}", file=sys.stderr)

    if dry_run:
        return 0

    # Validate syntax before executing
    check = subprocess.run(
        [shell, "-n", "-c", cmd],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        print(f"error: invalid bash syntax", file=sys.stderr)
        if check.stderr.strip():
            print(check.stderr.strip(), file=sys.stderr)
        return 2

    # Brief delay — gives time to read and CTRL+C if needed
    try:
        time.sleep(1.5)
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130

    return subprocess.call([shell, "-c", cmd])


if __name__ == "__main__":
    sys.exit(main())
