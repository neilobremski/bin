"""n0b CLI tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

N0B_PY = Path(__file__).resolve().parents[1] / "n0b.py"


def run_n0b(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(N0B_PY), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_help():
    proc = run_n0b("--help")
    assert proc.returncode == 0
    assert "json" in proc.stdout
    assert "ai" in proc.stdout


def test_json_pretty_print():
    proc = subprocess.run(
        [sys.executable, str(N0B_PY), "json"],
        input='{"b":1,"a":2}',
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)
    assert parsed == {"b": 1, "a": 2}


def test_ports_free():
    proc = run_n0b("ports", "free")
    assert proc.returncode == 0
    port = int(proc.stdout.strip())
    assert 1 <= port <= 65535


def test_secrets_from_env(monkeypatch):
    monkeypatch.setenv("N0B_TEST_SECRET", "hello")
    proc = run_n0b("secrets", "get", "N0B_TEST_SECRET")
    assert proc.returncode == 0
    assert proc.stdout == "hello"


def test_secrets_missing():
    proc = run_n0b("secrets", "get", "N0B_NONEXISTENT_SECRET_XYZ")
    assert proc.returncode == 1
    assert "not found" in proc.stderr
