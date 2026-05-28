"""q3w — NLP to bash command. LLM must produce a program, not speak.

Uses l9m to generate a bash command from natural language, then executes it.
The LLM's only output channel is structured: an executable command.

Safety: command is printed, validated as parseable bash, then a second LLM
call evaluates danger (this IS the delay — gives time to read and CTRL+C).

Default behavior: generate → print → validate → safety check → execute.
Use -n/--dry-run to print without executing.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

L9M_DIR = Path(__file__).resolve().parent.parent / "l9m"
sys.path.insert(0, str(L9M_DIR))

import l9m

GRAY = "\033[0;90m"
YELLOW = "\033[0;33m"
RESET = "\033[0m"

def _looks_dangerous(cmd: str, model: str) -> bool:
    """Ask the LLM if the command looks dangerous. Returns True if dangerous."""
    check_prompt = l9m.assemble_prompt(
        f"Is this bash command dangerous or destructive? Could it delete files, "
        f"kill processes, modify system config, or cause data loss?\n\n"
        f"Command: {cmd}",
        "bool", "Evaluate command safety", "",
    )
    try:
        answer = l9m.generate(model, check_prompt, stream=None)
    except l9m.L9mError:
        return False
    return answer.strip().upper().startswith("YES")


def main() -> int:
    argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help", "help"):
        print("""q3w — NLP to bash command

usage: q3w [options] <natural language...>

options:
  -n, --dry-run   Print command without executing
  -f, --force     Skip danger prompt (still warns)

flow: generate → print → validate → safety check → execute
the LLM must produce a program — it doesn't get to speak directly""")
        return 0

    dry_run = False
    force = False
    words = []

    for arg in argv:
        if arg in ("--dry-run", "-n"):
            dry_run = True
        elif arg in ("--force", "-f"):
            force = True
        else:
            words.append(arg)

    if not words:
        print("usage: q3w <natural language command>", file=sys.stderr)
        return 1

    prompt = " ".join(words)
    shell = os.environ.get("SHELL", "/bin/bash")
    instruction = f"I am using {shell}"

    try:
        model = l9m.resolve_model()
    except l9m.L9mError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    context_limit = l9m.resolve_context_limit(model)
    rolling = l9m._read_context()
    context = f"<Memories>\n{rolling}\n</Memories>" if rolling else ""
    full_prompt = l9m.assemble_prompt(prompt, "bash", instruction, context)

    try:
        output = l9m.generate(model, full_prompt, stream=None)
    except l9m.L9mError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

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
        l9m._append_context(prompt, cmd, context_limit)
        print(cmd)
        return 0

    # Validate syntax before executing
    check = subprocess.run(
        [shell, "-n", "-c", cmd],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        print("error: invalid bash syntax", file=sys.stderr)
        if check.stderr.strip():
            print(check.stderr.strip(), file=sys.stderr)
        return 2

    # Safety check — LLM evaluates the command for danger
    if _looks_dangerous(cmd, model):
        if force:
            print(f"{YELLOW}warning: command flagged as dangerous{RESET}", file=sys.stderr)
        else:
            try:
                answer = input(f"{YELLOW}dangerous command — execute? [y/N]{RESET} ")
            except (KeyboardInterrupt, EOFError):
                print("\naborted", file=sys.stderr)
                return 130
            if answer.strip().lower() not in ("y", "yes"):
                print("aborted", file=sys.stderr)
                return 130

    proc = subprocess.run(
        [shell, "-c", cmd],
        capture_output=True, text=True,
    )

    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)

    output_lines = []
    for line in (proc.stdout or "").splitlines():
        output_lines.append(f"STDOUT: {line}")
    for line in (proc.stderr or "").splitlines():
        output_lines.append(f"STDERR: {line}")
    if output_lines:
        result_text = cmd + "\n" + "\n".join(output_lines)
    else:
        result_text = cmd
    l9m._append_context(prompt, result_text, context_limit)

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
