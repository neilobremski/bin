"""TCP port utilities."""
from __future__ import annotations

import re
import shutil
import socket
import subprocess
import sys


def cmd_free() -> int:
    sock = socket.socket()
    try:
        sock.bind(("", 0))
        print(sock.getsockname()[1])
        return 0
    finally:
        sock.close()


def _listen_unix(port: int) -> int:
    proc = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        print(proc.stderr or proc.stdout, file=sys.stderr)
        return proc.returncode or 1
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) <= 1:
        print(f"No process listening on port {port}")
        return 0
    print("\n".join(lines))
    return 0


def _listen_windows(port: int) -> int:
    proc = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stderr or "netstat failed", file=sys.stderr)
        return proc.returncode or 1
    pattern = re.compile(rf":{port}\s")
    matches = [ln for ln in proc.stdout.splitlines() if pattern.search(ln)]
    if not matches:
        print(f"No process listening on port {port}")
        return 0
    print("\n".join(matches))
    return 0


def cmd_listen(port: int) -> int:
    if port < 1 or port > 65535:
        print("port must be between 1 and 65535", file=sys.stderr)
        return 1
    if sys.platform == "win32":
        return _listen_windows(port)
    if not shutil.which("lsof"):
        print("lsof not found on PATH", file=sys.stderr)
        return 1
    return _listen_unix(port)
