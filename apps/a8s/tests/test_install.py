"""Tests for `a8s install` — localized and global skill installation."""
from __future__ import annotations

import os
from pathlib import Path

from commands import cmd_install


def test_install_help(capsys):
    rc = cmd_install(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "a8s install" in out
    assert "--global" in out


def test_install_local_creates_claude_skill_symlink(tmp_path):
    agent = tmp_path / "agent"
    agent.mkdir()
    rc = cmd_install([str(agent)])
    assert rc == 0
    skill = agent / ".claude" / "skills" / "tell" / "SKILL.md"
    assert skill.is_symlink()
    assert skill.resolve().name == "SKILL.md"
    assert "tell" in skill.read_text()
    cursor = agent / ".cursor" / "skills" / "tell" / "SKILL.md"
    assert cursor.is_symlink()
    assert cursor.resolve() == skill.resolve()


def test_install_default_cwd(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    agent.mkdir()
    monkeypatch.chdir(agent)
    rc = cmd_install([])
    assert rc == 0
    assert (agent / ".claude" / "skills" / "tell" / "SKILL.md").is_symlink()


def test_install_global_uses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = cmd_install(["--global"])
    assert rc == 0
    skill = tmp_path / ".claude" / "skills" / "tell" / "SKILL.md"
    assert skill.is_symlink()
    cursor = tmp_path / ".cursor" / "skills" / "tell" / "SKILL.md"
    assert cursor.is_symlink()


def test_install_global_conflicts_with_path(capsys):
    rc = cmd_install(["--global", "/tmp"])
    assert rc != 0
    assert "conflicts" in capsys.readouterr().err


def test_install_not_a_directory(tmp_path, capsys):
    bad = tmp_path / "missing"
    rc = cmd_install([str(bad)])
    assert rc == 1
    assert "not a directory" in capsys.readouterr().err


def test_install_idempotent(tmp_path):
    agent = tmp_path / "agent"
    agent.mkdir()
    assert cmd_install([str(agent)]) == 0
    assert cmd_install([str(agent)]) == 0
    assert (agent / ".claude" / "skills" / "tell" / "SKILL.md").is_symlink()


def test_install_refuses_non_symlink_target(tmp_path, capsys):
    agent = tmp_path / "agent"
    agent.mkdir()
    dest = agent / ".claude" / "skills" / "tell" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("blocked")
    rc = cmd_install([str(agent)])
    assert rc == 0
    combined = capsys.readouterr().out + capsys.readouterr().err
    assert "refusing to overwrite" in combined
