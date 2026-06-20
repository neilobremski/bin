"""JSON pretty-print via stdlib json.tool."""
from __future__ import annotations

import subprocess
import sys


def cmd_json(args: list[str]) -> int:
    rc = subprocess.run([sys.executable, "-m", "json.tool", *args]).returncode
    return rc if rc is not None else 1
