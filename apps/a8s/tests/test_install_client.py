"""Tests for `a8s install-client`."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from commands import cmd_install_client


def test_install_client_help(capsys):
    rc = cmd_install_client(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "install-client" in out
    assert "/usr/local/lib/a8s" in out


def test_install_client_requires_root_for_usr_local(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    rc = cmd_install_client([])
    assert rc == 1


def test_install_client_positional_dest(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    lib_dir = tmp_path / "opt" / "a8s"
    bin_dir = tmp_path / "bin"
    assert cmd_install_client([str(lib_dir), "--bin-dir", str(bin_dir)]) == 0
    assert (lib_dir / "apps" / "a8s" / "a8s.py").is_file()
    assert (bin_dir / "tell").is_file()


def test_install_client_dest_conflicts_with_lib_dir(capsys):
    rc = cmd_install_client(["/tmp/a", "--lib-dir", "/tmp/b"])
    assert rc != 0


def test_install_client_copies_a8s_and_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    bin_dir = tmp_path / "bin"
    lib_dir = tmp_path / "lib" / "a8s"
    a8s_dest = lib_dir / "apps" / "a8s"

    assert cmd_install_client(["--bin-dir", str(bin_dir), "--lib-dir", str(lib_dir)]) == 0
    tell_bin = bin_dir / "tell"
    assert tell_bin.is_file()
    assert tell_bin.stat().st_mode & 0o777 == 0o755
    assert (a8s_dest / "a8s.py").is_file()
    assert (a8s_dest / "tell.py").is_file()
    assert not (a8s_dest / "tests").exists()
    assert (a8s_dest / "tell.py").stat().st_mode & 0o777 == 0o644

    (a8s_dest / "tell.py").write_text("# stale\n", encoding="utf-8")
    assert cmd_install_client(["--bin-dir", str(bin_dir), "--lib-dir", str(lib_dir)]) == 0
    assert "stale" not in (a8s_dest / "tell.py").read_text(encoding="utf-8")


def test_installed_tell_writes_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    bin_dir = tmp_path / "bin"
    lib_dir = tmp_path / "lib" / "a8s"
    assert cmd_install_client(["--bin-dir", str(bin_dir), "--lib-dir", str(lib_dir)]) == 0

    agent = tmp_path / "agent"
    outbox = agent / ".outbox"
    outbox.mkdir(parents=True)
    proc = subprocess.run(
        [str(bin_dir / "tell"), "GEMINI", "hello from client"],
        cwd=str(agent),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    files = list(outbox.glob("*.json"))
    assert len(files) == 1
    msg = json.loads(files[0].read_text(encoding="utf-8"))
    assert msg["to"] == "GEMINI"
    assert msg["content"] == "hello from client"
    assert "from" not in msg
