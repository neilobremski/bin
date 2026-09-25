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


def _pins_path() -> Path:
    return Path.home() / "lib" / ".secret-pins"


def _pinned_names() -> set[str]:
    path = _pins_path()
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def _is_pinned(name: str) -> bool:
    return name in _pinned_names()


def _pin(name: str) -> None:
    names = _pinned_names()
    if name in names:
        return
    names.add(name)
    path = _pins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text("\n".join(sorted(names)) + "\n")


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
    pinned = _is_pinned(name)
    if not pinned:
        val = os.environ.get(name)
        if val:
            return val
    path = _lib_path(name)
    if path.is_file():
        return path.read_text().replace("\n", "")
    kc = _keychain_get(name)
    if kc:
        return kc
    if pinned:
        val = os.environ.get(name)
        if val:
            return val
    return None


def resolve_trace(name: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    pinned = _is_pinned(name)
    env_val = os.environ.get(name)
    if env_val:
        results.append({"source": "env", "value": env_val})
    path = _lib_path(name)
    if path.is_file():
        results.append({"source": str(path), "value": path.read_text().replace("\n", "")})
    kc = _keychain_get(name)
    if kc:
        results.append({"source": "keychain", "value": kc})
    selected = None
    if pinned:
        for r in results:
            if r["source"] != "env":
                selected = r["source"]
                break
        if selected is None and env_val:
            selected = "env"
    else:
        if results:
            selected = results[0]["source"]
    for r in results:
        r["selected"] = r["source"] == selected
    return results


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


def cmd_trace(name: str) -> int:
    results = resolve_trace(name)
    pinned = _is_pinned(name)
    if not results:
        print(
            f"error: {name} not found (env, {_lib_path(name)}, or keychain)",
            file=sys.stderr,
        )
        return 1
    if pinned:
        print(f"  [{name} is pinned — file/keychain checked before env]",
              file=sys.stderr)
    for r in results:
        marker = " <-- selected" if r["selected"] else ""
        masked = r["value"][:4] + "..." if len(r["value"]) > 4 else r["value"]
        print(f"  {r['source']}: {masked}{marker}", file=sys.stderr)
    return 0


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
        _pin(name)
        print(f"set {name} in keychain (pinned)", file=sys.stderr)
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
    if not base_dir:
        _pin(name)
    print(f"set {name} in {path}{' (pinned)' if not base_dir else ''}", file=sys.stderr)
    return 0
