"""n0b CLI tests."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

N0B_PY = Path(__file__).resolve().parents[1] / "n0b.py"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commands.ai_cmd import cmd_ai  # noqa: E402
from commands.secrets_cmd import cmd_set, resolve  # noqa: E402


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


def test_secrets_set_and_resolve(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("N0B_SET_SECRET", raising=False)
    assert cmd_set("N0B_SET_SECRET", "s3cret") == 0
    path = tmp_path / "lib" / "n0b-set-secret.txt"
    assert path.read_text() == "s3cret\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert resolve("N0B_SET_SECRET") == "s3cret"


def test_secrets_set_dir(tmp_path):
    assert cmd_set("MY_KEY", "v", base_dir=str(tmp_path)) == 0
    assert (tmp_path / "my-key.txt").read_text() == "v\n"


def test_secrets_set_stdin(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(N0B_PY), "secrets", "set", "PIPED_KEY", "--dir", str(tmp_path)],
        input="fromstdin\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert (tmp_path / "piped-key.txt").read_text() == "fromstdin\n"


def test_secrets_set_empty_value_rejected(tmp_path):
    assert cmd_set("MY_KEY", "  ", base_dir=str(tmp_path)) == 1
    assert not (tmp_path / "my-key.txt").exists()


def test_secrets_set_env_file_upsert(tmp_path):
    env_file = tmp_path / "some.env"
    env_file.write_text("OTHER=1\nMY_KEY=old\n")
    assert cmd_set("MY_KEY", "new", env_file=str(env_file)) == 0
    assert env_file.read_text() == "OTHER=1\nMY_KEY=new\n"


def test_secrets_set_keychain_invokes_security():
    with patch("commands.secrets_cmd.subprocess.run") as run, \
            patch("commands.secrets_cmd.sys.platform", "darwin"):
        run.return_value.returncode = 0
        assert cmd_set("KC_KEY", "v", keychain=True) == 0
        argv = run.call_args[0][0]
        assert argv[:2] == ["security", "add-generic-password"]
        assert "KC_KEY" in argv and "v" in argv


def test_secrets_get_keychain_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KC_ONLY_KEY", raising=False)
    with patch("commands.secrets_cmd.subprocess.run") as run, \
            patch("commands.secrets_cmd.sys.platform", "darwin"):
        run.return_value.returncode = 0
        run.return_value.stdout = "kcval\n"
        assert resolve("KC_ONLY_KEY") == "kcval"
        argv = run.call_args[0][0]
        assert argv[:2] == ["security", "find-generic-password"]


def test_secrets_set_where_flags_exclusive(tmp_path):
    proc = run_n0b("secrets", "set", "X", "v", "--keychain", "--env-file", "x.env")
    assert proc.returncode == 2


def test_ai_research_requires_prompt():
    proc = run_n0b("ai", "research")
    assert proc.returncode == 2
    assert "Usage: n0b ai research" in proc.stderr


def test_ai_video_ltx2_passes_flag():
    with patch("commands.ai_cmd.subprocess.run") as run:
        run.return_value.returncode = 0
        rc = cmd_ai("video", "ltx-2", ["hello"])
        assert rc == 0
        argv = run.call_args[0][0]
        assert argv[0] == "bash"
        assert argv[2] == "--ltx2"
        assert argv[3] == "hello"
