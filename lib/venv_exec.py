#!/usr/bin/env python3
"""Install one dependency group and exec a script in the shared runtime."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from venv_util import ensure_group

PROBES = {
    "b3t": "import openpyxl, requests",
    "dev": "import pytest",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("usage: venv_exec.py GROUP [--] COMMAND [ARGS...]", file=sys.stderr)
        return 2
    group, command, *args = argv
    if command == "--" and not args:
        print("venv_exec.py: command required after --", file=sys.stderr)
        return 2
    try:
        python = ensure_group(group, probe=PROBES.get(group))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"{group}: dependency setup failed: {exc}", file=sys.stderr)
        return 1
    python_args = args if command == "--" else [str(Path(command).resolve()), *args]
    os.execvpe(str(python), [str(python), *python_args], os.environ.copy())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
