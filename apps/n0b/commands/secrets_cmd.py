"""Resolve secrets from environment, ~/lib files, or macOS Keychain."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _lib_path(name: str, base_dir: str | None = None) -> Path:
    file_name = name.lower().replace("_", "-") + ".txt"
    base = Path(base_dir) if base_dir else Path.home() / "lib"
    return base / file_name


def _keychain_get(name: str) -> str | None:
    if sys.platform != "darwin":
        return None
    proc = subprocess.run(
        ["security", "find-generic-password", "-s", name, "-w"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.rstrip("\n")


def resolve(name: str) -> str | None:
    val = os.environ.get(name)
    if val:
        return val
    path = _lib_path(name)
    if path.is_file():
        return path.read_text().replace("\n", "")
    return _keychain_get(name)


def cmd_get(name: str) -> int:
    val = resolve(name)
    if val:
        print(val, end="")
        return 0
    print(
        f"error: {name} not found (env, {_lib_path(name)}, or keychain)",
        file=sys.stderr,
    )
    return 1


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(content)


def cmd_set(
    name: str,
    value: str | None,
    base_dir: str | None = None,
    keychain: bool = False,
    env_file: str | None = None,
) -> int:
    if value is None:
        value = sys.stdin.read()
    value = value.strip()
    if not value:
        print("error: empty value", file=sys.stderr)
        return 1

    if keychain:
        if sys.platform != "darwin":
            print("error: --keychain requires macOS", file=sys.stderr)
            return 1
        proc = subprocess.run(
            [
                "security", "add-generic-password",
                "-a", os.environ.get("USER", ""),
                "-s", name,
                "-w", value,
                "-U",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            msg = proc.stderr.strip() or "security add-generic-password failed"
            print(f"error: {msg}", file=sys.stderr)
            return 1
        print(f"set {name} in keychain", file=sys.stderr)
        return 0

    if env_file:
        path = Path(env_file)
        lines = []
        if path.is_file():
            lines = [
                line
                for line in path.read_text().splitlines()
                if not line.startswith(f"{name}=")
            ]
        lines.append(f"{name}={value}")
        _write_private(path, "\n".join(lines) + "\n")
        print(f"set {name} in {path}", file=sys.stderr)
        return 0

    path = _lib_path(name, base_dir)
    _write_private(path, value + "\n")
    print(f"set {name} in {path}", file=sys.stderr)
    return 0
